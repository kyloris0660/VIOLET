#!/usr/bin/env python3
"""Phase 3.8d-I7 partial import and classification-first pipeline.

This runner is intentionally scoped to the Phase 3.8d-I6 item ledger.  It
derives the DB import set from rows that were actually staged and marked
eligible for DB import, then runs the existing import/classification/AI/
localization services only for the newly imported rows.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable, Sequence

from sqlalchemy import func, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import import_staged_manifest as staged_import  # noqa: E402

SOURCE_LABEL = "violet:phase3.8d:i7:staged-success"
CONFIRM_PHRASE = "IMPORT_PHASE38D_I7_STAGED_SUCCESS_994_TO_DB"
EXPECTED_TOTAL_ROWS = 1000
EXPECTED_STAGED_ROWS = 994
EXPECTED_FAILED_ROWS = {799, 839, 922, 970, 971, 972}
EXPECTED_REPLACEMENT_ROWS = {1029, 1041}
DEFERRED_ORIGINAL_ROWS = {98, 881}

DEFAULT_LEDGER = REPO_ROOT / ".local_manifests" / "phase-3.8d-i6-staging-copy-item-ledger.json"
DEFAULT_I6_DETAILS = REPO_ROOT / ".local_manifests" / "phase-3.8d-i6-staging-copy-retry-details.json"
DEFAULT_IMPORT_MANIFEST = REPO_ROOT / ".local_manifests" / "phase-3.8d-i7-staged-success-import-manifest.csv"
DEFAULT_IMPORT_LEDGER = REPO_ROOT / ".local_manifests" / "phase-3.8d-i7-import-item-ledger.json"
DEFAULT_VALIDATION_DETAILS = REPO_ROOT / ".local_manifests" / "phase-3.8d-i7-validation-details.json"
DEFAULT_REPORT_MD = REPO_ROOT / "docs" / "reports" / "phase-3.8d-i7-partial-import-classification-first.md"
DEFAULT_SUMMARY_JSON = REPO_ROOT / "docs" / "reports" / "phase-3.8d-i7-partial-import-classification-first-summary.json"

WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"(?i)\b[A-Z]:\\[^\n\r\"']*")
FILE_URI_RE = re.compile(r"file://", re.IGNORECASE)
POSIX_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_:])/(?:mnt|Volumes|workspace|home|Users|var|tmp|private|opt|srv|data)"
    r"(?:/[^\s\"']*)*"
)


class PhaseI7Error(RuntimeError):
    """Raised for fail-closed I7 gates."""


@dataclass
class ImportCandidate:
    row_id: int
    safe_label: str
    bucket: str
    extension: str
    expected_size: int
    target_safe_label: str
    staged_path: Path
    file_hash: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    return str(value)


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PhaseI7Error(f"missing_json:{path.name}") from exc
    except UnicodeDecodeError as exc:
        raise PhaseI7Error(f"invalid_json_encoding:{path.name}") from exc
    except json.JSONDecodeError as exc:
        raise PhaseI7Error(f"malformed_json:{path.name}") from exc
    if not isinstance(data, dict):
        raise PhaseI7Error(f"json_not_object:{path.name}")
    return data


def write_json(path: Path, data: MappingLike) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=json_default) + "\n", encoding="utf-8")


MappingLike = dict[str, Any]


def safe_label(value: Any, *, fallback: str = "unknown") -> str:
    text_value = str(value or "").replace("\\", "/").strip()
    if not text_value:
        return fallback
    return text_value.split("/")[-1] or fallback


def sanitize_public_text(value: Any) -> str:
    text_value = str(value)
    text_value = WINDOWS_ABSOLUTE_PATH_RE.sub("[redacted_path]", text_value)
    text_value = POSIX_ABSOLUTE_PATH_RE.sub("[redacted_path]", text_value)
    text_value = FILE_URI_RE.sub("[redacted_file_uri]", text_value)
    return text_value


def scan_privacy_leaks(data: Any) -> list[str]:
    text_value = json.dumps(data, ensure_ascii=False, default=json_default)
    leaks = []
    if WINDOWS_ABSOLUTE_PATH_RE.search(text_value):
        leaks.append("windows_absolute_path")
    if POSIX_ABSOLUTE_PATH_RE.search(text_value):
        leaks.append("posix_absolute_path")
    if FILE_URI_RE.search(text_value):
        leaks.append("file_uri")
    return leaks


def media_ids_identity_proof(media_ids: Sequence[int]) -> dict[str, Any]:
    normalized = sorted({int(media_id) for media_id in media_ids})
    digest = hashlib.sha256(",".join(map(str, normalized)).encode("utf-8")).hexdigest()
    return {
        "media_ids_count": len(normalized),
        "media_ids_sha256": digest,
        "media_ids_sample": normalized[:20],
    }


def prior_classification_matches_media_ids(prior: Any, media_ids: Sequence[int]) -> bool:
    if not isinstance(prior, dict) or prior.get("status") != "completed":
        return False
    proof = prior.get("identity_proof")
    if not isinstance(proof, dict):
        return False
    expected = media_ids_identity_proof(media_ids)
    return (
        int(proof.get("media_ids_count") or -1) == expected["media_ids_count"]
        and str(proof.get("media_ids_sha256") or "") == expected["media_ids_sha256"]
    )


def build_safe_privacy_blocked_summary(
    summary: MappingLike,
    *,
    summary_leaks: Sequence[str] | None = None,
    markdown_leaks: Sequence[str] | None = None,
) -> dict[str, Any]:
    return {
        "phase": "3.8d-I7",
        "source_label": SOURCE_LABEL,
        "status": "blocked_public_report_privacy_leak",
        "success": False,
        "started_at": summary.get("started_at"),
        "finished_at": utc_now(),
        "privacy_scan": {
            "summary_leaks": sorted(set(summary_leaks or [])),
            "markdown_leaks": sorted(set(markdown_leaks or [])),
            "summary_leak_count": len(set(summary_leaks or [])),
            "markdown_leak_count": len(set(markdown_leaks or [])),
            "public_outputs_fail_closed": True,
        },
        "safe_blocked_report": True,
        "paths_redacted": True,
    }


def render_safe_privacy_blocked_report(summary: MappingLike) -> str:
    privacy = summary.get("privacy_scan", {}) if isinstance(summary.get("privacy_scan"), dict) else {}
    lines = [
        "# Phase 3.8d-I7 Partial Import Classification-first Pipeline",
        "",
        "## Summary",
        "- Status: `blocked_public_report_privacy_leak`",
        "- Success: `False`",
        "- Public output fail-closed before persisting unsafe report content.",
        "",
        "## Privacy Gate",
        f"- Summary leak count: `{privacy.get('summary_leak_count', 0)}`",
        f"- Markdown leak count: `{privacy.get('markdown_leak_count', 0)}`",
        f"- Leak reasons: `{sorted(set((privacy.get('summary_leaks') or []) + (privacy.get('markdown_leaks') or [])))}`",
        "- Unsafe fields were not persisted in the public report.",
        "- Local diagnostic details remain local/untracked when present.",
    ]
    return "\n".join(lines) + "\n"


def resolve_under_root(label: str, root: Path) -> Path:
    if PureWindowsPath(label).is_absolute() or Path(label).is_absolute():
        raise PhaseI7Error(f"target_safe_label_must_be_relative:{safe_label(label)}")
    path = (root / Path(label)).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise PhaseI7Error(f"target_path_escape:{safe_label(label)}") from exc
    return path


def load_i6_target_root(details_path: Path) -> Path:
    details = read_json(details_path)
    raw = details.get("target_root") or details.get("expected_staging_root")
    if not raw:
        raise PhaseI7Error("missing_i6_target_root")
    target_root = Path(str(raw)).expanduser().resolve()
    if not target_root.exists() or not target_root.is_dir():
        raise PhaseI7Error("i6_target_root_missing_or_not_directory")
    return target_root


def validate_i6_item_ledger(
    ledger_path: Path,
    target_root: Path,
    *,
    expected_total: int = EXPECTED_TOTAL_ROWS,
    expected_staged: int = EXPECTED_STAGED_ROWS,
    expected_failed_rows: set[int] | None = None,
) -> tuple[list[ImportCandidate], dict[str, Any]]:
    expected_failed_rows = expected_failed_rows or set(EXPECTED_FAILED_ROWS)
    data = read_json(ledger_path)
    rows = data.get("rows")
    if not isinstance(rows, list):
        raise PhaseI7Error("item_ledger_rows_missing")
    if len(rows) != expected_total:
        raise PhaseI7Error(f"item_ledger_total_mismatch:{len(rows)}")

    candidates: list[ImportCandidate] = []
    failed_rows: list[int] = []
    duplicate_targets: set[str] = set()
    seen_targets: set[str] = set()
    missing_staged: list[int] = []
    size_mismatch: list[int] = []
    unstaged_eligible: list[int] = []
    failed_eligible: list[int] = []

    for row in rows:
        row_id = int(row.get("row_id"))
        status = str(row.get("status") or "")
        eligible = bool(row.get("eligible_for_db_import"))
        if status == "staged" and eligible:
            target_label = str(row.get("target_safe_label") or "")
            target_path = resolve_under_root(target_label, target_root)
            target_key = str(target_path).lower()
            if target_key in seen_targets:
                duplicate_targets.add(safe_label(target_label))
            seen_targets.add(target_key)
            expected_size = int(row.get("expected_size") or 0)
            if not target_path.exists() or not target_path.is_file():
                missing_staged.append(row_id)
            else:
                actual_size = target_path.stat().st_size
                if actual_size != expected_size:
                    size_mismatch.append(row_id)
            candidates.append(
                ImportCandidate(
                    row_id=row_id,
                    safe_label=str(row.get("safe_label") or f"source_row_{row_id:04d}"),
                    bucket=str(row.get("bucket") or ""),
                    extension=str(row.get("extension") or "").lower(),
                    expected_size=expected_size,
                    target_safe_label=target_label,
                    staged_path=target_path,
                )
            )
        elif eligible:
            unstaged_eligible.append(row_id)
        else:
            failed_rows.append(row_id)
            if row_id not in expected_failed_rows:
                pass
            if status == "staged":
                failed_eligible.append(row_id)

    failed_set = set(failed_rows)
    if failed_set != expected_failed_rows:
        raise PhaseI7Error(
            "failed_row_set_mismatch:"
            + json.dumps({"actual": sorted(failed_set), "expected": sorted(expected_failed_rows)})
        )
    if len(candidates) != expected_staged:
        raise PhaseI7Error(f"staged_candidate_count_mismatch:{len(candidates)}")
    if duplicate_targets:
        raise PhaseI7Error("duplicate_target_path:" + ",".join(sorted(duplicate_targets)))
    if missing_staged:
        raise PhaseI7Error("missing_staged_files:" + ",".join(map(str, missing_staged[:20])))
    if size_mismatch:
        raise PhaseI7Error("staged_size_mismatch:" + ",".join(map(str, size_mismatch[:20])))
    if unstaged_eligible:
        raise PhaseI7Error("unstaged_row_marked_eligible:" + ",".join(map(str, unstaged_eligible[:20])))
    if failed_eligible:
        raise PhaseI7Error("failed_row_marked_eligible:" + ",".join(map(str, failed_eligible[:20])))

    candidate_ids = {item.row_id for item in candidates}
    forbidden_candidates = candidate_ids & DEFERRED_ORIGINAL_ROWS
    if forbidden_candidates:
        raise PhaseI7Error("deferred_original_row_is_import_candidate:" + ",".join(map(str, sorted(forbidden_candidates))))
    present_replacements = candidate_ids & EXPECTED_REPLACEMENT_ROWS
    missing_replacements = EXPECTED_REPLACEMENT_ROWS - present_replacements
    if missing_replacements:
        raise PhaseI7Error("replacement_row_missing_from_import_candidates:" + ",".join(map(str, sorted(missing_replacements))))

    summary = {
        "status": "passed",
        "ledger_basename": ledger_path.name,
        "details_basename": DEFAULT_I6_DETAILS.name,
        "total_rows": len(rows),
        "staged_success": len(candidates),
        "failed_items": len(failed_rows),
        "failed_rows": sorted(failed_rows),
        "excluded_failed_rows": sorted(failed_rows),
        "deferred_original_rows_excluded": sorted(DEFERRED_ORIGINAL_ROWS),
        "replacement_rows_present": sorted(present_replacements),
        "target_paths_checked": len(seen_targets),
        "missing_staged_files": 0,
        "size_mismatches": 0,
        "duplicate_target_paths": 0,
        "paths_redacted": True,
    }
    return candidates, summary


def write_import_manifest(candidates: Sequence[ImportCandidate], output_path: Path) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "row_id",
        "source_path",
        "proposed_target_path",
        "extension",
        "size_bytes",
        "selection_reason",
        "exclusion_reason",
        "placeholder_flag",
        "safe_label",
        "target_safe_label",
        "bucket",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in candidates:
            staged = str(item.staged_path)
            writer.writerow(
                {
                    "row_id": item.row_id,
                    "source_path": staged,
                    "proposed_target_path": staged,
                    "extension": item.extension,
                    "size_bytes": item.expected_size,
                    "selection_reason": "staged_success_i6",
                    "exclusion_reason": "",
                    "placeholder_flag": "false",
                    "safe_label": item.safe_label,
                    "target_safe_label": item.target_safe_label,
                    "bucket": item.bucket,
                }
            )
    return {
        "basename": output_path.name,
        "rows": len(candidates),
        "contains_staged_paths": True,
        "contains_source_icloud_paths": False,
        "committed": False,
    }


def build_import_candidates(candidates: Sequence[ImportCandidate]) -> list[staged_import.ManifestCandidate]:
    result: list[staged_import.ManifestCandidate] = []
    for index, item in enumerate(candidates, start=2):
        result.append(
            staged_import.ManifestCandidate(
                row_number=index,
                row_id=str(item.row_id),
                source_path=str(item.staged_path),
                proposed_target_path=str(item.staged_path),
                extension=item.extension,
                size_bytes=item.expected_size,
                selection_reason="staged_success_i6",
            )
        )
    return result


def create_db_engine(context: staged_import.RuntimeContext) -> Engine:
    return staged_import.create_db_engine(context)


def db_count(engine: Engine, table: str) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)


def database_counts(engine: Engine, source_label: str) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            "media": int(conn.execute(text("SELECT COUNT(*) FROM blombooru_media")).scalar() or 0),
            "media_source_label": int(
                conn.execute(text("SELECT COUNT(*) FROM blombooru_media WHERE source = :source"), {"source": source_label}).scalar()
                or 0
            ),
            "media_tags": int(conn.execute(text("SELECT COUNT(*) FROM blombooru_media_tags")).scalar() or 0),
            "ai_jobs": int(conn.execute(text("SELECT COUNT(*) FROM blombooru_ai_tag_jobs")).scalar() or 0),
            "classification_jobs": int(conn.execute(text("SELECT COUNT(*) FROM blombooru_classification_jobs")).scalar() or 0),
            "translation_jobs": int(conn.execute(text("SELECT COUNT(*) FROM blombooru_tag_translation_jobs")).scalar() or 0),
        }


def active_job_counts(engine: Engine) -> dict[str, int]:
    statuses = ("pending", "running", "cancelling")
    result: dict[str, int] = {}
    with engine.connect() as conn:
        for key, table in [
            ("ai_jobs", "blombooru_ai_tag_jobs"),
            ("classification_jobs", "blombooru_classification_jobs"),
            ("translation_jobs", "blombooru_tag_translation_jobs"),
        ]:
            result[key] = int(
                conn.execute(
                    text(f"SELECT COUNT(*) FROM {table} WHERE status = ANY(:statuses)"),
                    {"statuses": list(statuses)},
                ).scalar()
                or 0
            )
    return result


def verify_db_identity(context: staged_import.RuntimeContext, engine: Engine) -> dict[str, Any]:
    if context.violet_env != "development":
        raise PhaseI7Error(f"unexpected_violet_env:{context.violet_env}")
    if context.db_name != "blombooru":
        raise PhaseI7Error(f"unexpected_db_name:{context.db_name}")
    with engine.connect() as conn:
        db_name = str(conn.execute(text("SELECT current_database()")).scalar() or "")
        if db_name != "blombooru":
            raise PhaseI7Error(f"connected_db_mismatch:{db_name}")
    active = active_job_counts(engine)
    if any(active.values()):
        raise PhaseI7Error("active_background_jobs:" + json.dumps(active))
    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=REPO_ROOT, text=True).strip()
    if branch != "phase3.8d-i7-partial-import-classification-first":
        raise PhaseI7Error(f"unexpected_branch:{branch}")
    return {
        "violet_env": context.violet_env,
        "db_name": context.db_name,
        "db_host": context.database_url.host,
        "db_port": context.database_url.port,
        "storage_root_label": "app_storage",
        "repo_root_label": "repo_root",
        "branch": branch,
        "git_sha": git_sha,
        "active_jobs": active,
        "paths_redacted": True,
    }


def create_pg_dump_backup(context: staged_import.RuntimeContext, backup_path: Path) -> dict[str, Any]:
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        candidate = Path("C:/Program Files/PostgreSQL/17/bin/pg_dump.exe")
        if candidate.exists():
            pg_dump = str(candidate)
    if not pg_dump:
        raise PhaseI7Error("pg_dump_not_found")

    env = os.environ.copy()
    if context.database_url.password is not None:
        env["PGPASSWORD"] = context.database_url.password
    cmd = [
        pg_dump,
        "-Fc",
        "-f",
        str(backup_path),
        "-h",
        str(context.database_url.host or "localhost"),
        "-p",
        str(context.database_url.port or 5432),
        "-U",
        str(context.database_url.username or "postgres"),
        str(context.database_url.database or context.db_name),
    ]
    completed = subprocess.run(cmd, cwd=REPO_ROOT, env=env, text=True, capture_output=True, timeout=1800)
    if completed.returncode != 0:
        safe_error = sanitize_public_text((completed.stderr or completed.stdout or "pg_dump failed")[:1000])
        raise PhaseI7Error(f"pg_dump_failed:{safe_error}")
    size = backup_path.stat().st_size if backup_path.exists() else 0
    if size <= 0:
        raise PhaseI7Error("pg_dump_empty")
    return {"basename": backup_path.name, "bytes": size, "path_redacted": True}


def run_import_dry_run(
    candidates: Sequence[ImportCandidate],
    target_root: Path,
    context: staged_import.RuntimeContext,
    engine: Engine,
) -> tuple[list[staged_import.ImportItem], dict[str, Any]]:
    staged_import.IMPORT_SOURCE_LABEL = SOURCE_LABEL
    manifest_candidates = build_import_candidates(candidates)
    valid, invalid, total_bytes = staged_import.validate_candidates(manifest_candidates, target_root)
    hashes = [item.file_hash for item in valid if item.file_hash]
    existing_by_hash = staged_import.get_existing_media_by_hash(engine, hashes)
    items = staged_import.build_import_items(valid, invalid, existing_by_hash)
    counts = staged_import._item_counts(items, dry_run=True)
    failed_rows_included = sorted(EXPECTED_FAILED_ROWS & {int(item.candidate.row_id) for item in items})
    unstaged_rows_included = sorted(
        int(item.candidate.row_id)
        for item in items
        if int(item.candidate.row_id) in EXPECTED_FAILED_ROWS or int(item.candidate.row_id) in DEFERRED_ORIGINAL_ROWS
    )
    dry_run = {
        "status": "passed" if not invalid and not failed_rows_included and not unstaged_rows_included else "blocked",
        "checked_count": len(manifest_candidates),
        "valid_count": len(valid),
        "invalid_rows": len(invalid),
        "missing_staged_files": sum(1 for item in invalid if "missing staged file" in (item.invalid_reason or "")),
        "duplicate_by_hash": counts["duplicates_by_hash"],
        "would_create": counts["would_create"],
        "manifest_internal_hash_duplicates": counts["manifest_internal_hash_duplicates"],
        "total_staged_bytes_checked": total_bytes,
        "failed_rows_included": bool(failed_rows_included),
        "failed_rows_included_ids": failed_rows_included,
        "unstaged_rows_included": bool(unstaged_rows_included),
        "unstaged_rows_included_ids": unstaged_rows_included,
        "source_label": SOURCE_LABEL,
        "app_managed_storage_writes_estimated": counts["would_create"],
        "db_public_fields_full_path_free": True,
        "paths_redacted": True,
    }
    if dry_run["status"] != "passed":
        raise PhaseI7Error("import_dry_run_blocked")
    return items, dry_run


def execute_import(
    items: list[staged_import.ImportItem],
    context: staged_import.RuntimeContext,
    engine: Engine,
) -> tuple[list[staged_import.ImportItem], dict[str, Any]]:
    staged_import.IMPORT_SOURCE_LABEL = SOURCE_LABEL
    context.original_dir.mkdir(parents=True, exist_ok=True)
    context.thumbnail_dir.mkdir(parents=True, exist_ok=True)
    before_counts = staged_import._item_counts(items, dry_run=True)
    executed = staged_import.execute_import_items(items, context, engine)
    after_counts = staged_import._item_counts(executed, dry_run=False)
    imported_ids = [int(item.media_id) for item in executed if item.status == "imported" and item.media_id]
    failed = [item for item in executed if item.status == "failed"]
    result = {
        "status": "completed" if not failed else "failed_item_import",
        "source_label": SOURCE_LABEL,
        "input_would_create": before_counts["would_create"],
        "imported_count": len(imported_ids),
        "duplicate_by_hash": after_counts["duplicates_by_hash"],
        "failed_count": after_counts["failed"],
        "invalid_count": after_counts["invalid"],
        "imported_media_ids_sample": imported_ids[:20],
        "imported_media_ids_count": len(imported_ids),
        "app_managed_storage_writes": len(imported_ids),
        "db_stores_app_relative_paths_only": True,
    }
    if failed:
        result["failed_rows"] = [
            {
                "row_id": int(item.candidate.row_id),
                "safe_label": safe_label(item.candidate.source_path),
                "reason": sanitize_public_text(item.message)[:500],
            }
            for item in failed[:20]
        ]
    return executed, result


def item_status_from_import(item: staged_import.ImportItem) -> dict[str, Any]:
    row_id = int(item.candidate.row_id)
    if item.status == "imported":
        return {
            "row_id": row_id,
            "import_status": "imported",
            "imported_media_id": item.media_id,
            "eligible_for_downstream": True,
            "managed_label": safe_label(item.managed_path),
            "thumbnail_label": safe_label(item.thumbnail_path),
        }
    if item.status == "duplicate_by_hash":
        return {
            "row_id": row_id,
            "import_status": "duplicate_by_hash",
            "duplicate_media_id": item.duplicate_media_id,
            "eligible_for_downstream": False,
        }
    return {
        "row_id": row_id,
        "import_status": item.status,
        "reason": sanitize_public_text(item.message),
        "eligible_for_downstream": False,
    }


def write_import_item_ledger(
    source_ledger_path: Path,
    import_items: Sequence[staged_import.ImportItem],
    output_path: Path,
    downstream: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = read_json(source_ledger_path)
    import_by_row = {int(item.candidate.row_id): item_status_from_import(item) for item in import_items}
    rows = []
    for row in source.get("rows", []):
        row_id = int(row.get("row_id"))
        public_row = {
            "row_id": row_id,
            "safe_label": row.get("safe_label"),
            "bucket": row.get("bucket"),
            "extension": row.get("extension"),
            "i6_status": row.get("status"),
            "i6_reason": row.get("reason"),
            "i6_eligible_for_db_import": bool(row.get("eligible_for_db_import")),
            "target_safe_label": row.get("target_safe_label"),
            "import": import_by_row.get(
                row_id,
                {
                    "row_id": row_id,
                    "import_status": "excluded_i6_failed",
                    "eligible_for_downstream": False,
                },
            ),
        }
        rows.append(public_row)
    ledger = {
        "created_at": utc_now(),
        "phase": "3.8d-I7",
        "source_label": SOURCE_LABEL,
        "rows": rows,
        "downstream": downstream or {},
    }
    write_json(output_path, ledger)
    imported = sum(1 for row in rows if row["import"].get("import_status") == "imported")
    duplicates = sum(1 for row in rows if row["import"].get("import_status") == "duplicate_by_hash")
    excluded = sum(1 for row in rows if row["import"].get("import_status") == "excluded_i6_failed")
    return {
        "basename": output_path.name,
        "rows": len(rows),
        "imported": imported,
        "duplicate_by_hash": duplicates,
        "excluded_i6_failed": excluded,
        "committed": False,
    }


def get_db_session() -> Session:
    from app import database

    if database.SessionLocal is None:
        database.init_engine()
    if database.SessionLocal is None:
        raise PhaseI7Error("database_session_not_initialized")
    return database.SessionLocal()


def load_prior_import_items(validation_details_path: Path, candidates: Sequence[ImportCandidate]) -> list[staged_import.ImportItem]:
    prior = read_json(validation_details_path)
    raw_items = prior.get("import_items")
    if not isinstance(raw_items, list):
        raise PhaseI7Error("resume_missing_prior_import_items")
    candidate_by_row = {int(item.row_id): item for item in build_import_candidates(candidates)}
    result: list[staged_import.ImportItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        row_id = int(raw.get("row_id"))
        candidate = candidate_by_row.get(row_id)
        if candidate is None:
            continue
        status = str(raw.get("status") or "")
        if status != "imported" or not raw.get("media_id"):
            raise PhaseI7Error("resume_prior_import_item_not_imported")
        result.append(
            staged_import.ImportItem(
                candidate=candidate,
                status="imported",
                media_id=int(raw["media_id"]),
                managed_path=str(raw.get("managed_path") or ""),
                thumbnail_path=str(raw.get("thumbnail_path") or ""),
                message="resumed from prior successful I7 import attempt",
            )
        )
    if len(result) != len(candidates):
        raise PhaseI7Error(f"resume_prior_import_count_mismatch:{len(result)}")
    return result


def media_ids_for_source(engine: Engine, source_label: str) -> list[int]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id FROM blombooru_media WHERE source = :source ORDER BY id ASC"),
            {"source": source_label},
        ).all()
    return [int(row[0]) for row in rows]


def media_rows_for_source(engine: Engine, source_label: str) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT id, path, thumbnail_path, hash "
                    "FROM blombooru_media WHERE source = :source ORDER BY id ASC"
                ),
                {"source": source_label},
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


def resume_import_items_from_db_source(
    engine: Engine,
    dry_run_items: Sequence[staged_import.ImportItem],
    *,
    expected_count: int,
) -> list[staged_import.ImportItem]:
    rows = media_rows_for_source(engine, SOURCE_LABEL)
    if len(rows) != expected_count:
        raise PhaseI7Error(f"resume_db_source_label_count_mismatch:{len(rows)}")

    by_hash: dict[str, dict[str, Any]] = {}
    duplicate_hashes: set[str] = set()
    for row in rows:
        file_hash = str(row.get("hash") or "")
        if not file_hash:
            raise PhaseI7Error("resume_db_source_label_row_missing_hash")
        if file_hash in by_hash:
            duplicate_hashes.add(file_hash)
        by_hash[file_hash] = row
    if duplicate_hashes:
        raise PhaseI7Error(f"resume_db_source_label_duplicate_hashes:{len(duplicate_hashes)}")

    result: list[staged_import.ImportItem] = []
    missing_hash_rows: list[str] = []
    for item in dry_run_items:
        if item.status != "duplicate_by_hash":
            raise PhaseI7Error(f"resume_dry_run_item_not_duplicate:{item.candidate.row_id}")
        file_hash = item.candidate.file_hash or ""
        row = by_hash.get(file_hash)
        if row is None:
            missing_hash_rows.append(str(item.candidate.row_id))
            continue
        result.append(
            staged_import.ImportItem(
                candidate=item.candidate,
                status="imported",
                media_id=int(row["id"]),
                managed_path=str(row.get("path") or ""),
                thumbnail_path=str(row.get("thumbnail_path") or ""),
                message="resumed from current DB source-label media row",
            )
        )
    if missing_hash_rows:
        raise PhaseI7Error("resume_db_source_label_hash_mismatch:" + ",".join(missing_hash_rows[:20]))
    if len(result) != expected_count:
        raise PhaseI7Error(f"resume_db_source_label_import_item_count_mismatch:{len(result)}")
    return result


def validate_source_label_import_coverage(
    engine: Engine,
    import_items: Sequence[staged_import.ImportItem],
    *,
    expected_count: int,
) -> tuple[dict[str, Any], list[int], list[staged_import.ImportItem]]:
    rows = media_rows_for_source(engine, SOURCE_LABEL)
    expected_by_hash: dict[str, staged_import.ImportItem] = {}
    duplicate_candidate_hashes: set[str] = set()
    missing_candidate_hash_rows: list[str] = []
    for item in import_items:
        row_id = str(item.candidate.row_id)
        file_hash = str(item.candidate.file_hash or "")
        if not file_hash:
            missing_candidate_hash_rows.append(row_id)
            continue
        if file_hash in expected_by_hash:
            duplicate_candidate_hashes.add(file_hash)
        expected_by_hash[file_hash] = item

    rows_by_hash: dict[str, dict[str, Any]] = {}
    duplicate_source_label_hashes: set[str] = set()
    missing_db_hash_count = 0
    for row in rows:
        file_hash = str(row.get("hash") or "")
        if not file_hash:
            missing_db_hash_count += 1
            continue
        if file_hash in rows_by_hash:
            duplicate_source_label_hashes.add(file_hash)
        rows_by_hash[file_hash] = row

    expected_hashes = set(expected_by_hash)
    source_hashes = set(rows_by_hash)
    missing_source_label_hashes = sorted(expected_hashes - source_hashes)
    unexpected_source_label_hashes = sorted(source_hashes - expected_hashes)

    coverage = {
        "status": "passed",
        "source_label": SOURCE_LABEL,
        "expected_import_candidate_count": expected_count,
        "source_label_media_count": len(rows),
        "candidate_hash_count": len(expected_hashes),
        "source_label_hash_count": len(source_hashes),
        "missing_candidate_hash_rows_count": len(missing_candidate_hash_rows),
        "duplicate_candidate_hash_count": len(duplicate_candidate_hashes),
        "missing_db_hash_count": missing_db_hash_count,
        "duplicate_source_label_hash_count": len(duplicate_source_label_hashes),
        "missing_source_label_hash_count": len(missing_source_label_hashes),
        "unexpected_source_label_hash_count": len(unexpected_source_label_hashes),
        "identity_match": False,
        "identity_source": "db_source_label_hash_match",
        "paths_redacted": True,
    }

    if len(rows) != expected_count:
        coverage["status"] = (
            "blocked_import_coverage_incomplete"
            if len(rows) < expected_count
            else "blocked_import_coverage_unexpected_extra"
        )
    elif len(expected_hashes) != expected_count or missing_candidate_hash_rows or duplicate_candidate_hashes:
        coverage["status"] = "blocked_import_candidate_hash_identity_invalid"
    elif (
        missing_db_hash_count
        or duplicate_source_label_hashes
        or missing_source_label_hashes
        or unexpected_source_label_hashes
    ):
        coverage["status"] = "blocked_import_coverage_hash_mismatch"
    else:
        coverage["identity_match"] = True

    if coverage["status"] != "passed":
        coverage["sample_missing_source_label_row_ids"] = [
            int(expected_by_hash[file_hash].candidate.row_id) for file_hash in missing_source_label_hashes[:20]
        ]
        coverage["sample_unexpected_source_label_media_ids"] = [
            int(rows_by_hash[file_hash]["id"]) for file_hash in unexpected_source_label_hashes[:20]
        ]
        return coverage, [], []

    covered_items: list[staged_import.ImportItem] = []
    for file_hash, item in expected_by_hash.items():
        row = rows_by_hash[file_hash]
        covered_items.append(
            staged_import.ImportItem(
                candidate=item.candidate,
                status="imported",
                media_id=int(row["id"]),
                managed_path=str(row.get("path") or ""),
                thumbnail_path=str(row.get("thumbnail_path") or ""),
                message="covered by current DB SOURCE_LABEL media row",
            )
        )
    covered_items.sort(key=lambda item: int(item.candidate.row_id))
    media_ids = sorted(int(row["id"]) for row in rows)
    coverage["downstream_media_ids_count"] = len(media_ids)
    coverage["downstream_media_ids_sample"] = media_ids[:20]
    return coverage, media_ids, covered_items


def content_class_distribution(db: Session, media_ids: Sequence[int]) -> dict[str, int]:
    from app.models import Media

    result = {"anime": 0, "unknown": 0, "non_anime": 0, "illustration": 0, "failed_or_unclassified": 0}
    if not media_ids:
        return result
    rows = (
        db.query(Media.content_class, func.count(Media.id))
        .filter(Media.id.in_(list(media_ids)))
        .group_by(Media.content_class)
        .all()
    )
    for content_class, count in rows:
        raw = getattr(content_class, "value", content_class)
        key = raw or "failed_or_unclassified"
        if key not in result:
            key = "failed_or_unclassified"
        result[key] += int(count)
    return result


def normalize_content_class(value: Any) -> str | None:
    raw = getattr(value, "value", value)
    if raw is None:
        return None
    return str(raw)


def build_classification_resume_from_records(
    imported_media_ids: Sequence[int],
    records: Sequence[MappingLike],
    *,
    identity_source: str,
) -> dict[str, Any]:
    expected_ids = {int(media_id) for media_id in imported_media_ids}
    found_ids = {int(record.get("id")) for record in records if record.get("id") is not None}
    if found_ids != expected_ids:
        raise PhaseI7Error(
            "classification_resume_media_id_set_mismatch:"
            + json.dumps(
                {
                    "expected_count": len(expected_ids),
                    "found_count": len(found_ids),
                    "missing_count": len(expected_ids - found_ids),
                    "unexpected_count": len(found_ids - expected_ids),
                },
                sort_keys=True,
            )
        )

    distribution = {"anime": 0, "unknown": 0, "non_anime": 0, "illustration": 0, "failed_or_unclassified": 0}
    unclassified: list[int] = []
    for record in records:
        media_id = int(record.get("id"))
        content_class = normalize_content_class(record.get("content_class"))
        if content_class is None:
            unclassified.append(media_id)
            distribution["failed_or_unclassified"] += 1
        elif content_class in distribution:
            distribution[content_class] += 1
        else:
            distribution["failed_or_unclassified"] += 1

    if unclassified:
        raise PhaseI7Error(f"classification_resume_unclassified_media:{len(unclassified)}")

    proof = media_ids_identity_proof(list(expected_ids))
    proof["identity_source"] = identity_source
    return {
        "status": "completed",
        "jobs": [],
        "processed": len(expected_ids),
        "failed": distribution["failed_or_unclassified"],
        "distribution": distribution,
        "resume_status": "resumed_with_identity_proof",
        "identity_proof": proof,
    }


def build_classification_resume_from_db(imported_media_ids: Sequence[int]) -> dict[str, Any]:
    if not imported_media_ids:
        return {"status": "noop_no_imported_media", "jobs": [], "processed": 0, "failed": 0, "distribution": {}}
    db = get_db_session()
    try:
        from app.models import Media

        rows = (
            db.query(Media.id, Media.content_class)
            .filter(Media.source == SOURCE_LABEL)
            .filter(Media.id.in_(list(imported_media_ids)))
            .all()
        )
        records = [{"id": int(row.id), "content_class": row.content_class} for row in rows]
    finally:
        db.close()
    return build_classification_resume_from_records(
        imported_media_ids,
        records,
        identity_source="db_source_label_content_class",
    )


def resolve_classification_resume(imported_media_ids: Sequence[int], prior_classification: Any) -> dict[str, Any]:
    if prior_classification_matches_media_ids(prior_classification, imported_media_ids):
        classification = dict(prior_classification)
        classification["resume_status"] = "resumed_with_prior_identity_proof"
        classification["identity_proof_source"] = "validation_details_identity_proof"
        return classification
    return build_classification_resume_from_db(imported_media_ids)


def run_classification(media_ids: Sequence[int], chunk_size: int) -> dict[str, Any]:
    from app.config import settings
    from app.services.classification_job_service import create_classification_job, run_classification_job

    if not media_ids:
        return {"status": "noop_no_imported_media", "jobs": [], "processed": 0, "failed": 0, "distribution": {}}
    effective_chunk = min(max(1, chunk_size), int(settings.CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS))
    jobs = []
    for start in range(0, len(media_ids), effective_chunk):
        chunk = list(media_ids[start : start + effective_chunk])
        db = get_db_session()
        try:
            job = create_classification_job(
                db,
                media_ids=chunk,
                max_items=len(chunk),
                only_unclassified=False,
                force_reclassify=False,
                trigger_source="phase3.8d-i7",
            )
            job_id = int(job.id)
        finally:
            db.close()
        run_classification_job(job_id)
        db = get_db_session()
        try:
            from app.models import ClassificationJob

            refreshed = db.query(ClassificationJob).get(job_id)
            jobs.append(
                {
                    "job_id": job_id,
                    "status": refreshed.status if refreshed else "missing",
                    "processed": int(getattr(refreshed, "processed", 0) or 0),
                    "failed": int(getattr(refreshed, "failed", 0) or 0),
                    "classified_anime": int(getattr(refreshed, "classified_anime", 0) or 0),
                    "classified_non_anime": int(getattr(refreshed, "classified_non_anime", 0) or 0),
                    "classified_unknown": int(getattr(refreshed, "classified_unknown", 0) or 0),
                    "error_message": sanitize_public_text(getattr(refreshed, "error_message", "") or "")[:500],
                }
            )
        finally:
            db.close()
        if jobs[-1]["status"] not in {"completed"}:
            break
    db = get_db_session()
    try:
        distribution = content_class_distribution(db, media_ids)
    finally:
        db.close()
    failed = sum(job["failed"] for job in jobs)
    status = "completed" if all(job["status"] == "completed" for job in jobs) else "failed"
    return {
        "status": status,
        "jobs": jobs,
        "processed": sum(job["processed"] for job in jobs),
        "failed": failed,
        "distribution": distribution,
    }


def select_i7_ai_eligible_media_ids(imported_media_ids: Sequence[int]) -> tuple[list[int], dict[str, Any]]:
    from app.models import Media
    from app.services.classification_first_workflow import (
        assert_ai_scope_media_ids_are_eligible,
        is_eligible_content_class,
    )

    db = get_db_session()
    try:
        if not imported_media_ids:
            return [], {"eligible_count": 0, "skipped_ineligible": 0, "source_label": SOURCE_LABEL}
        rows = (
            db.query(Media.id, Media.content_class)
            .filter(Media.source == SOURCE_LABEL)
            .filter(Media.id.in_(list(imported_media_ids)))
            .order_by(Media.id.asc())
            .all()
        )
        eligible = [int(row.id) for row in rows if is_eligible_content_class(row.content_class)]
        assert_ai_scope_media_ids_are_eligible(db, eligible, source_label=SOURCE_LABEL)
        return eligible, {
            "eligible_count": len(eligible),
            "skipped_ineligible": len(rows) - len(eligible),
            "source_label": SOURCE_LABEL,
        }
    finally:
        db.close()


def run_ai_tagging(media_ids: Sequence[int], chunk_size: int) -> dict[str, Any]:
    from app.config import settings
    from app.services.ai_tagging_job_service import create_ai_tag_job, run_ai_tag_job

    os.environ["CONTENT_CLASSIFICATION_ENABLED"] = "false"
    os.environ["AI_TAGGING_AUTO_LOCALIZATION"] = "false"
    os.environ["TAG_TRANSLATION_AUTO_ENABLED"] = "false"
    os.environ["TAG_TRANSLATION_BACKGROUND_ENABLED"] = "false"

    if not media_ids:
        return {"status": "noop_no_eligible_media", "jobs": [], "eligible_count": 0, "processed": 0, "failed": 0}
    effective_chunk = min(max(1, chunk_size), int(settings.AI_TAGGING_BATCH_MAX_ITEMS))
    jobs = []
    for start in range(0, len(media_ids), effective_chunk):
        chunk = list(media_ids[start : start + effective_chunk])
        db = get_db_session()
        try:
            job = create_ai_tag_job(
                db,
                media_ids=chunk,
                max_items=len(chunk),
                dry_run=False,
                only_without_ai_tags=True,
                force_suggestions=False,
                trigger_source="phase3.8d-i7",
            )
            job_id = int(job.id)
        finally:
            db.close()
        run_ai_tag_job(job_id)
        db = get_db_session()
        try:
            from app.models import AITagJob

            refreshed = db.query(AITagJob).get(job_id)
            jobs.append(
                {
                    "job_id": job_id,
                    "status": refreshed.status if refreshed else "missing",
                    "processed": int(getattr(refreshed, "processed", 0) or 0),
                    "failed": int(getattr(refreshed, "failed", 0) or 0),
                    "tags_added": int(getattr(refreshed, "tags_added", 0) or 0),
                    "suggestions_added": int(getattr(refreshed, "suggestions_added", 0) or 0),
                    "skipped_locked": int(getattr(refreshed, "skipped_locked", 0) or 0),
                    "ignored_low_confidence": int(getattr(refreshed, "ignored_low_confidence", 0) or 0),
                    "localization_status": getattr(refreshed, "localization_status", None),
                    "error_message": sanitize_public_text(getattr(refreshed, "error_message", "") or "")[:500],
                }
            )
        finally:
            db.close()
        if jobs[-1]["status"] not in {"completed"}:
            break
    return {
        "status": "completed" if all(job["status"] == "completed" for job in jobs) else "failed",
        "jobs": jobs,
        "eligible_count": len(media_ids),
        "processed": sum(job["processed"] for job in jobs),
        "failed": sum(job["failed"] for job in jobs),
        "tags_added": sum(job["tags_added"] for job in jobs),
        "suggestions_added": sum(job["suggestions_added"] for job in jobs),
        "skipped_locked": sum(job["skipped_locked"] for job in jobs),
        "ignored_low_confidence": sum(job["ignored_low_confidence"] for job in jobs),
    }


async def translate_with_provider(provider: Any, candidates: list[dict[str, Any]]) -> list[Any]:
    tag_inputs = [{"name": item["canonical_name"], "category": item["category"]} for item in candidates]
    return await provider.translate_tags(tag_inputs)


def mark_translation_job_failed(
    db: Session,
    job: Any,
    result: dict[str, Any],
    *,
    status: str,
    error_message: str,
    processed: int,
    translated: int,
    failed: int,
    skipped: int,
) -> dict[str, Any]:
    safe_error = sanitize_public_text(error_message)[:1000]
    try:
        db.rollback()
    except Exception:
        pass
    persisted = False
    try:
        job.status = "failed"
        job.processed = processed
        job.translated = translated
        job.failed = failed
        job.skipped = skipped
        job.error_message = safe_error
        job.last_error = safe_error
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        persisted = True
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        safe_error = sanitize_public_text(f"{safe_error}; failed_state_commit={exc.__class__.__name__}")[:1000]
    result.update(
        {
            "status": status,
            "processed": processed,
            "translated_count": translated,
            "failed_count": failed,
            "error": safe_error,
            "job_failed_state_persisted": persisted,
        }
    )
    return result


def persist_localization_translations(
    db: Session,
    job: Any,
    candidates: list[dict[str, Any]],
    translations: list[Any],
    *,
    lang: str,
    skipped_proper_nouns: int,
    provider_name: str,
    result: dict[str, Any],
    upsert_translation_fn: Any,
    remaining_candidates_fn: Any,
    invalidate_cache_fn: Any,
    sanitize_error_fn: Any,
) -> dict[str, Any]:
    candidate_by_name = {item["canonical_name"]: item for item in candidates}
    seen_outputs: set[str] = set()
    saved_names: set[str] = set()
    try:
        for translation in translations:
            canonical = getattr(translation, "canonical_name", "")
            if canonical not in candidate_by_name or canonical in seen_outputs:
                continue
            seen_outputs.add(canonical)
            item = candidate_by_name[canonical]
            saved = upsert_translation_fn(
                db,
                canonical_name=canonical,
                display_name=getattr(translation, "display_name_zh", ""),
                lang=lang,
                aliases=getattr(translation, "aliases_zh", []) or [],
                category=item["category"],
                source="llm",
                status="translated",
                confidence=getattr(translation, "confidence", None),
                needs_review=bool(getattr(translation, "needs_review", False)),
                provider=provider_name,
            )
            if saved is not None:
                saved_names.add(canonical)

        result["translated_count"] = len(saved_names)
        result["failed_count"] = max(0, len(candidates) - len(saved_names))
        job.status = "completed" if result["failed_count"] == 0 else "failed"
        job.processed = len(candidates)
        job.translated = result["translated_count"]
        job.failed = result["failed_count"]
        job.skipped = skipped_proper_nouns
        remaining = len(remaining_candidates_fn())
        job.remaining_after = remaining
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        invalidate_cache_fn()
        result["remaining_missing_translations"] = remaining
        result["status"] = "completed" if result["failed_count"] == 0 else "failed_partial"
        return result
    except Exception as exc:
        safe_error = sanitize_public_text(sanitize_error_fn(str(exc)))[:1000]
        return mark_translation_job_failed(
            db,
            job,
            result,
            status="failed_translation_persistence",
            error_message=safe_error,
            processed=len(candidates),
            translated=len(saved_names),
            failed=max(0, len(candidates) - len(saved_names)),
            skipped=skipped_proper_nouns,
        )


def run_localization(limit: int | None = None, lang: str = "zh-CN") -> dict[str, Any]:
    from app.config import settings
    from app.models import TagTranslationJob
    from app.services.classification_first_workflow import (
        LOCALIZABLE_CATEGORIES,
        PROPER_NOUN_CATEGORIES,
        select_eligible_localization_candidates,
    )
    from app.services.llm_translation_provider import _sanitize_error_message, get_llm_provider
    from app.services.tag_localization_service import upsert_translation
    from app.utils.search_parser import invalidate_translation_cache

    effective_limit = limit if limit is not None else int(settings.TAG_TRANSLATION_BATCH_MAX_ITEMS)
    db = get_db_session()
    try:
        candidates = select_eligible_localization_candidates(
            db,
            SOURCE_LABEL,
            lang=lang,
            categories=LOCALIZABLE_CATEGORIES,
            limit=effective_limit,
        )
        skipped_proper_nouns = len(
            select_eligible_localization_candidates(
                db,
                SOURCE_LABEL,
                lang=lang,
                categories=PROPER_NOUN_CATEGORIES,
                limit=None,
            )
        )
        result: dict[str, Any] = {
            "status": "running",
            "candidate_count": len(candidates),
            "translated_count": 0,
            "failed_count": 0,
            "skipped_proper_nouns": skipped_proper_nouns,
            "localizable_categories": list(LOCALIZABLE_CATEGORIES),
            "proper_noun_categories_skipped": list(PROPER_NOUN_CATEGORIES),
            "effective_limit": effective_limit,
            "job_id": None,
        }
        if not candidates:
            result["status"] = "noop_no_candidates"
            result["remaining_missing_translations"] = 0
            return result

        provider = get_llm_provider()
        result["provider_available"] = provider.is_available()
        if not provider.is_available():
            result["status"] = "failed_provider_unavailable"
            result["failed_count"] = len(candidates)
            return result

        job = TagTranslationJob(
            status="running",
            source="phase3.8d-i7",
            language=lang,
            category="general,meta",
            batch_size=min(len(candidates), effective_limit),
            max_per_run=effective_limit,
            processed=0,
            translated=0,
            failed=0,
            skipped=skipped_proper_nouns,
            remaining_before=len(candidates),
            started_at=datetime.now(timezone.utc),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        result["job_id"] = int(job.id)

        try:
            translations = asyncio.run(translate_with_provider(provider, candidates))
        except Exception as exc:
            safe_error = sanitize_public_text(_sanitize_error_message(str(exc)))[:1000]
            return mark_translation_job_failed(
                db,
                job,
                result,
                status="failed_provider_error",
                error_message=safe_error,
                processed=0,
                translated=0,
                failed=len(candidates),
                skipped=skipped_proper_nouns,
            )

        return persist_localization_translations(
            db,
            job,
            candidates,
            list(translations),
            lang=lang,
            skipped_proper_nouns=skipped_proper_nouns,
            provider_name=provider.get_provider_name(),
            result=result,
            upsert_translation_fn=upsert_translation,
            remaining_candidates_fn=lambda: select_eligible_localization_candidates(
                db,
                SOURCE_LABEL,
                lang=lang,
                categories=LOCALIZABLE_CATEGORIES,
                limit=None,
            ),
            invalidate_cache_fn=invalidate_translation_cache,
            sanitize_error_fn=_sanitize_error_message,
        )
    finally:
        db.close()


def localization_candidate_snapshot(lang: str = "zh-CN") -> dict[str, Any]:
    from app.services.classification_first_workflow import (
        LOCALIZABLE_CATEGORIES,
        PROPER_NOUN_CATEGORIES,
        select_eligible_localization_candidates,
    )

    db = get_db_session()
    try:
        localizable = select_eligible_localization_candidates(
            db,
            SOURCE_LABEL,
            lang=lang,
            categories=LOCALIZABLE_CATEGORIES,
            limit=None,
        )
        proper_nouns = select_eligible_localization_candidates(
            db,
            SOURCE_LABEL,
            lang=lang,
            categories=PROPER_NOUN_CATEGORIES,
            limit=None,
        )
    finally:
        db.close()
    return {
        "candidate_count": len(localizable),
        "skipped_proper_nouns": len(proper_nouns),
        "localizable_categories": list(LOCALIZABLE_CATEGORIES),
        "proper_noun_categories_skipped": list(PROPER_NOUN_CATEGORIES),
    }


def build_localization_continuation_scope(db_source_label_count: int) -> dict[str, Any]:
    if db_source_label_count <= 0:
        return {
            "status": "no_imported_media_for_source_label",
            "db_source_label_count": db_source_label_count,
            "expected_original_i7_candidate_count": EXPECTED_STAGED_ROWS,
            "partial_import_compatible": True,
        }
    return {
        "status": "passed",
        "db_source_label_count": db_source_label_count,
        "expected_original_i7_candidate_count": EXPECTED_STAGED_ROWS,
        "partial_import_compatible": True,
    }


def run_localization_continuation(args: argparse.Namespace) -> dict[str, Any]:
    try:
        summary = read_json(args.summary_json)
    except PhaseI7Error:
        summary = build_base_summary(args)
    if not isinstance(summary, dict):
        summary = build_base_summary(args)
    summary.setdefault("phase", "3.8d-I7")
    summary.setdefault("source_label", SOURCE_LABEL)

    context = staged_import.build_runtime_context(REPO_ROOT)
    engine = create_db_engine(context)
    try:
        summary["db_identity_closeout"] = verify_db_identity(context, engine)
        imported_media_ids = media_ids_for_source(engine, SOURCE_LABEL)
        scope = build_localization_continuation_scope(len(imported_media_ids))
        if scope["status"] != "passed":
            summary["status"] = str(scope["status"])
            summary["success"] = False
            summary["localization_continuation"] = {
                **scope,
                "db_import_reran": False,
                "classification_reran": False,
                "ai_tagging_reran": False,
            }
            summary["finished_at"] = utc_now()
            return summary

        before = localization_candidate_snapshot(lang=args.lang)
        candidate_count = int(before["candidate_count"])
        if candidate_count > args.max_additional_localization_candidates:
            summary["status"] = "blocked_unexpected_localization_candidate_count"
            summary["success"] = False
            summary["localization_continuation"] = {
                "status": "blocked_unexpected_localization_candidate_count",
                "additional_candidate_count": candidate_count,
                "max_additional_candidates": args.max_additional_localization_candidates,
                **before,
            }
            summary["finished_at"] = utc_now()
            return summary

        if candidate_count:
            continuation = run_localization(limit=candidate_count, lang=args.lang)
        else:
            continuation = {
                "status": "noop_no_candidates",
                "candidate_count": 0,
                "translated_count": 0,
                "failed_count": 0,
                "remaining_missing_translations": 0,
                **before,
            }
        after = localization_candidate_snapshot(lang=args.lang)
        initial = summary.get("localization", {}) if isinstance(summary.get("localization"), dict) else {}
        summary["localization_continuation"] = {
            **scope,
            "status": continuation.get("status"),
            "additional_candidate_count": candidate_count,
            "additional_translated_count": int(continuation.get("translated_count") or 0),
            "additional_failed_count": int(continuation.get("failed_count") or 0),
            "remaining_missing_general_meta_after": int(after["candidate_count"]),
            "skipped_proper_nouns": int(after["skipped_proper_nouns"]),
            "localizable_categories": after["localizable_categories"],
            "proper_noun_categories_skipped": after["proper_noun_categories_skipped"],
            "provider_available": continuation.get("provider_available"),
            "job_id": continuation.get("job_id"),
            "max_additional_candidates": args.max_additional_localization_candidates,
            "db_import_reran": False,
            "classification_reran": False,
            "ai_tagging_reran": False,
        }
        summary["localization_final"] = {
            "initial_translated_count": int(initial.get("translated_count") or 0),
            "initial_failed_count": int(initial.get("failed_count") or 0),
            "initial_remaining_missing_general_meta": int(initial.get("remaining_missing_translations") or candidate_count),
            "continuation_translated_count": int(continuation.get("translated_count") or 0),
            "continuation_failed_count": int(continuation.get("failed_count") or 0),
            "remaining_missing_general_meta_after": int(after["candidate_count"]),
            "proper_noun_categories_skipped": after["proper_noun_categories_skipped"],
        }
        apply_localization_continuation_status(summary, continuation, remaining_missing=int(after["candidate_count"]))
        summary["closeout_safety"] = {
            "db_import_reran": False,
            "classification_reran": False,
            "ai_tagging_reran": False,
            "staging_copy_reran": False,
            "source_icloud_write_mutation": False,
            "entity_resolver_ran": False,
            "similarity_or_clustering_ran": False,
        }
        summary["db_counts_after_closeout"] = database_counts(engine, SOURCE_LABEL)
        summary["finished_at"] = utc_now()
        return summary
    finally:
        engine.dispose()


def resolve_app_storage_path(storage_root: Path, raw_path: str | None) -> tuple[Path | None, str | None]:
    if raw_path is None or str(raw_path).strip() == "":
        return None, "missing_path"
    text_path = str(raw_path).strip()
    if FILE_URI_RE.search(text_path):
        return None, "file_uri_path"
    if PureWindowsPath(text_path).is_absolute() or Path(text_path).is_absolute():
        return None, "absolute_path"
    storage_root_resolved = storage_root.resolve()
    try:
        resolved = (storage_root_resolved / Path(text_path)).resolve()
        resolved.relative_to(storage_root_resolved)
    except (OSError, RuntimeError, ValueError):
        return None, "path_outside_storage_root"
    return resolved, None


def safe_existing_file(path: Path) -> tuple[bool, str | None]:
    try:
        return path.exists() and path.is_file(), None
    except (OSError, RuntimeError) as exc:
        return False, exc.__class__.__name__


def validate_imported_db_and_storage_rows(
    context: staged_import.RuntimeContext,
    imported_media_ids: Sequence[int],
    rows: Sequence[MappingLike],
) -> dict[str, Any]:
    expected_ids = {int(media_id) for media_id in imported_media_ids}
    found_ids = {int(row.get("id")) for row in rows if row.get("id") is not None}
    media_checked = len(rows)
    source_label_mismatches = 0
    privacy_leaks = 0
    original_exists = 0
    thumbnails_exist = 0
    missing_original_count = 0
    missing_thumbnail_count = 0
    path_containment_failures = 0
    storage_probe_failures = 0
    missing_db_rows = len(expected_ids - found_ids)
    unexpected_db_rows = len(found_ids - expected_ids)

    for row in rows:
        source = str(row.get("source") or "")
        if source != SOURCE_LABEL:
            source_label_mismatches += 1
        path = str(row.get("path") or "")
        thumb = str(row.get("thumbnail_path") or "")
        if scan_privacy_leaks({"path": path, "thumbnail_path": thumb}):
            privacy_leaks += 1

        original_path, original_path_error = resolve_app_storage_path(context.storage_root, path)
        if original_path_error:
            path_containment_failures += 1
            missing_original_count += 1
        elif original_path is not None:
            exists, probe_error = safe_existing_file(original_path)
            if exists:
                original_exists += 1
            else:
                missing_original_count += 1
            if probe_error:
                storage_probe_failures += 1

        thumbnail_path, thumbnail_path_error = resolve_app_storage_path(context.storage_root, thumb)
        if thumbnail_path_error:
            path_containment_failures += 1
            missing_thumbnail_count += 1
        elif thumbnail_path is not None:
            exists, probe_error = safe_existing_file(thumbnail_path)
            if exists:
                thumbnails_exist += 1
            else:
                missing_thumbnail_count += 1
            if probe_error:
                storage_probe_failures += 1

    status = (
        "passed"
        if (
            media_checked == len(imported_media_ids)
            and missing_db_rows == 0
            and unexpected_db_rows == 0
            and source_label_mismatches == 0
            and privacy_leaks == 0
            and original_exists == media_checked
            and thumbnails_exist == media_checked
            and missing_original_count == 0
            and missing_thumbnail_count == 0
            and path_containment_failures == 0
            and storage_probe_failures == 0
        )
        else "failed"
    )
    return {
        "status": status,
        "media_checked": media_checked,
        "expected_media_ids": len(imported_media_ids),
        "source_label_count": media_checked - source_label_mismatches,
        "source_label_mismatches": source_label_mismatches,
        "missing_db_rows": missing_db_rows,
        "unexpected_db_rows": unexpected_db_rows,
        "original_files_exist": original_exists,
        "thumbnails_exist": thumbnails_exist,
        "missing_original_count": missing_original_count,
        "missing_thumbnail_count": missing_thumbnail_count,
        "path_containment_failures": path_containment_failures,
        "storage_probe_failures": storage_probe_failures,
        "path_privacy_leaks": privacy_leaks,
        "source_icloud_paths_stored": False,
        "db_public_fields_full_path_free": privacy_leaks == 0 and path_containment_failures == 0,
    }


def validate_imported_db_and_storage(
    engine: Engine,
    context: staged_import.RuntimeContext,
    imported_media_ids: Sequence[int],
) -> dict[str, Any]:
    if not imported_media_ids:
        return {
            "status": "noop_no_imported_media",
            "media_checked": 0,
            "source_label_count": 0,
            "original_files_exist": 0,
            "thumbnails_exist": 0,
            "path_privacy_leaks": 0,
        }
    with engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT id, path, thumbnail_path, source FROM blombooru_media "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": list(imported_media_ids)},
            )
            .mappings()
            .all()
        )
    return validate_imported_db_and_storage_rows(context, imported_media_ids, [dict(row) for row in rows])


def apply_db_storage_validation_gate(summary: dict[str, Any], validation: MappingLike) -> bool:
    summary["db_storage_validation"] = dict(validation)
    if validation.get("status") != "passed":
        summary["status"] = "blocked_db_storage_validation_failed"
        summary["success"] = False
        return False
    return True


def apply_localization_continuation_status(
    summary: dict[str, Any],
    continuation: MappingLike,
    *,
    remaining_missing: int,
) -> None:
    if continuation.get("status") in {"completed", "noop_no_candidates"} and remaining_missing == 0:
        summary["status"] = "completed"
        summary["success"] = True
    elif continuation.get("status") == "failed_provider_unavailable":
        summary["status"] = "localization_continuation_provider_unavailable"
        summary["success"] = False
    else:
        summary["status"] = "completed_with_localization_continuation_failures"
        summary["success"] = False


def render_markdown_report(summary: MappingLike) -> str:
    ledger = summary.get("i6_item_ledger_validation", {})
    import_execute = summary.get("import_execute", {})
    import_coverage = summary.get("import_coverage", {})
    classification = summary.get("classification", {})
    ai = summary.get("ai_tagging", {})
    localization = summary.get("localization", {})
    localization_continuation = summary.get("localization_continuation", {})
    localization_final = summary.get("localization_final", {})
    api = summary.get("api_browser_smoke", {})
    lines = [
        "# Phase 3.8d-I7 Partial Import Classification-first Pipeline",
        "",
        "## Summary",
        f"- Status: `{summary.get('status')}`",
        f"- Success: `{summary.get('success')}`",
        f"- Source label: `{summary.get('source_label')}`",
        "- Scope: import only I6 staged-success rows, then classify first before AI tagging/localization.",
        "",
        "## I6 Item Ledger Gate",
        f"- Ledger validation: `{ledger.get('status')}`",
        f"- Total rows: `{ledger.get('total_rows')}`",
        f"- Import candidates: `{summary.get('import_candidate_count')}`",
        f"- Excluded failed rows: `{ledger.get('excluded_failed_rows')}`",
        f"- Deferred original rows excluded: `{ledger.get('deferred_original_rows_excluded')}`",
        f"- Replacement rows present: `{ledger.get('replacement_rows_present')}`",
        "",
        "## DB Backup And Import",
        f"- Backup: `{summary.get('db_backup', {}).get('basename')}` / `{summary.get('db_backup', {}).get('bytes')}` bytes",
        f"- Dry-run status: `{summary.get('import_dry_run', {}).get('status')}`",
        f"- Dry-run checked: `{summary.get('import_dry_run', {}).get('checked_count')}`",
        f"- Would create: `{summary.get('import_dry_run', {}).get('would_create')}`",
        f"- Duplicate by hash: `{summary.get('import_dry_run', {}).get('duplicate_by_hash')}`",
        f"- Import status: `{import_execute.get('status')}`",
        f"- Imported media count: `{import_execute.get('imported_count')}`",
        f"- Import failures: `{import_execute.get('failed_count')}`",
        f"- Resume note: `{import_execute.get('resume_reason', 'not_applicable')}`",
        f"- App-managed writes in final resume run: `{import_execute.get('app_managed_storage_writes')}`",
        f"- Prior successful import writes preserved: `{import_execute.get('previous_app_managed_storage_writes', 0)}`",
        f"- SOURCE_LABEL coverage: `{import_coverage.get('status', 'not_checked')}`",
        f"- SOURCE_LABEL media count: `{import_coverage.get('source_label_media_count')}`",
        f"- Downstream media scope: `{import_coverage.get('downstream_media_ids_count')}`",
        f"- Downstream identity source: `{import_coverage.get('identity_source', 'not_checked')}`",
        "",
        "## Classification-first Pipeline",
        f"- Classification status: `{classification.get('status')}`",
        f"- Classification processed: `{classification.get('processed')}`",
        f"- Classification failed: `{classification.get('failed')}`",
        f"- Distribution: `{classification.get('distribution')}`",
        f"- AI eligible count: `{ai.get('eligible_count')}`",
        f"- AI tagging status: `{ai.get('status')}`",
        f"- AI processed: `{ai.get('processed')}`",
        f"- AI failed: `{ai.get('failed')}`",
        f"- Suggestions added: `{ai.get('suggestions_added')}`",
        f"- Confirmed tags added: `{ai.get('tags_added')}`",
        f"- Localization status: `{localization.get('status')}`",
        f"- Localization candidates: `{localization.get('candidate_count')}`",
        f"- Translated: `{localization.get('translated_count')}`",
        f"- Localization failed: `{localization.get('failed_count')}`",
        f"- Skipped proper nouns: `{localization.get('skipped_proper_nouns')}`",
        f"- Localization continuation status: `{localization_continuation.get('status', 'not_run')}`",
        f"- Additional localization candidates: `{localization_continuation.get('additional_candidate_count', 0)}`",
        f"- Additional translated: `{localization_continuation.get('additional_translated_count', 0)}`",
        f"- Additional failed: `{localization_continuation.get('additional_failed_count', 0)}`",
        f"- Final remaining general/meta: `{localization_final.get('remaining_missing_general_meta_after', localization.get('remaining_missing_translations'))}`",
        f"- Proper noun categories skipped: `{localization_final.get('proper_noun_categories_skipped', localization.get('proper_noun_categories_skipped'))}`",
        "",
        "## Validation",
        f"- DB/storage validation: `{summary.get('db_storage_validation', {}).get('status')}`",
        f"- API/browser/admin smoke: `{api.get('status', 'pending')}`",
        f"- Server log scan: `{summary.get('server_log_scan', {}).get('status', 'pending')}`",
        "",
        "## Tests",
        f"- Diff check: `{summary.get('tests', {}).get('git_diff_check')}`",
        f"- Py compile: `{summary.get('tests', {}).get('py_compile')}`",
        f"- Focused tests: `{summary.get('tests', {}).get('focused_pytest')}`",
        f"- Full non-E2E suite: `{summary.get('tests', {}).get('full_non_e2e_pytest')}`",
        "",
        "## Safety Confirmation",
        "- Failed I6 rows were not imported.",
        "- Raw 1000 manifest was not used for import.",
        "- Source/iCloud write mutation: NO.",
        "- Staging copy rerun: NO.",
        "- Entity Resolver / similarity / clustering: NO.",
        "- App-managed storage writes occurred only through approved DB import.",
        "- Full 1000 DB import remains blocked; downstream planning must use the I7 item ledger.",
        "",
        "## Engineering Judgment / Operator Notes",
        "- The phase boundary is appropriate as a recovery path: importing the 994 staged-success rows is safer than retrying cloud failures before every downstream validation.",
        "- Remaining risk is operational: duplicate-by-hash or downstream job failures can reduce newly imported/downstream-eligible counts and must stay item-scoped.",
        "- Failed I6 rows remain deferred; future work should decide retry/backfill/partial-import policy separately.",
        "- This phase intentionally does not start Entity Resolver, similarity, clustering, or Phase 4.",
        "",
        "## Privacy",
        "- Public report contains safe labels and aggregate counts only.",
        "- Local full-path manifests/details remain ignored and uncommitted.",
    ]
    return "\n".join(lines) + "\n"


def build_base_summary(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "phase": "3.8d-I7",
        "source_label": SOURCE_LABEL,
        "started_at": utc_now(),
        "status": "running",
        "success": False,
        "mode": "execute" if args.execute else "dry_run",
        "persistent_rule_verified": "Agent Engineering Judgment and Bugfix Root-Cause Closure Policy",
        "safety": {
            "staging_copy_reran": False,
            "source_icloud_write_mutation": False,
            "entity_resolver_ran": False,
            "similarity_or_clustering_ran": False,
            "raw_1000_manifest_used_for_import": False,
            "failed_i6_rows_imported": False,
        },
    }


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    summary = build_base_summary(args)
    try:
        existing_details = read_json(args.validation_details)
    except PhaseI7Error:
        existing_details = {}
    local_details: dict[str, Any] = existing_details if isinstance(existing_details, dict) else {}
    local_details.update({"updated_at": utc_now(), "phase": "3.8d-I7"})

    target_root = load_i6_target_root(args.i6_details)
    candidates, ledger_summary = validate_i6_item_ledger(args.ledger, target_root)
    summary["i6_item_ledger_validation"] = ledger_summary
    summary["import_candidate_count"] = len(candidates)
    summary["excluded_failed_rows"] = sorted(EXPECTED_FAILED_ROWS)
    manifest_summary = write_import_manifest(candidates, args.import_manifest)
    summary["import_manifest"] = manifest_summary
    local_details["import_manifest_path"] = args.import_manifest
    local_details["target_root"] = target_root

    context = staged_import.build_runtime_context(REPO_ROOT)
    engine = create_db_engine(context)
    try:
        summary["db_identity"] = verify_db_identity(context, engine)
        summary["db_counts_before"] = database_counts(engine, SOURCE_LABEL)
        items, dry_run = run_import_dry_run(candidates, target_root, context, engine)
        summary["import_dry_run"] = dry_run
        if not args.execute:
            summary["status"] = "dry_run_passed"
            summary["success"] = True
            summary["finished_at"] = utc_now()
            local_details["dry_run_items"] = [
                {
                    "row_id": item.candidate.row_id,
                    "status": item.status,
                    "media_id": item.media_id,
                    "message": item.message,
                }
                for item in items
            ]
            return summary

        if args.confirm_import != CONFIRM_PHRASE:
            raise PhaseI7Error("invalid_confirm_import_phrase")
        backup_path = args.db_backup_file or (
            REPO_ROOT
            / ".local_manifests"
            / f"phase-3.8d-i7-db-backup-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.dump"
        )
        summary["db_backup"] = create_pg_dump_backup(context, backup_path)

        source_media_ids_before_execute = media_ids_for_source(engine, SOURCE_LABEL)
        if (
            dry_run["would_create"] == 0
            and dry_run["duplicate_by_hash"] == len(candidates)
            and len(source_media_ids_before_execute) == len(candidates)
        ):
            executed_items = resume_import_items_from_db_source(engine, items, expected_count=len(candidates))
            import_result = {
                "status": "resumed_after_prior_successful_import",
                "source_label": SOURCE_LABEL,
                "input_would_create": 0,
                "imported_count": len(executed_items),
                "duplicate_by_hash": dry_run["duplicate_by_hash"],
                "failed_count": 0,
                "invalid_count": 0,
                "imported_media_ids_sample": [int(item.media_id) for item in executed_items if item.media_id][:20],
                "imported_media_ids_count": len([item for item in executed_items if item.media_id]),
                "app_managed_storage_writes": 0,
                "previous_app_managed_storage_writes": len(executed_items),
                "db_stores_app_relative_paths_only": True,
                "resume_reason": "current DB source-label rows prove previous I7 import completed",
                "resume_identity_source": "db_source_label_hash_match",
                "resume_db_source_label_count": len(source_media_ids_before_execute),
            }
        else:
            executed_items, import_result = execute_import(items, context, engine)
        summary["import_execute"] = import_result
        if import_result["status"] != "resumed_after_prior_successful_import":
            local_details["import_items"] = [
                {
                    "row_id": item.candidate.row_id,
                    "status": item.status,
                    "media_id": item.media_id,
                    "duplicate_media_id": item.duplicate_media_id,
                    "managed_path": item.managed_path,
                    "thumbnail_path": item.thumbnail_path,
                    "message": item.message,
                }
                for item in executed_items
            ]
        if import_result["failed_count"]:
            summary["status"] = "blocked_import_failure"
            summary["success"] = False
            return summary

        import_coverage, imported_media_ids, downstream_import_items = validate_source_label_import_coverage(
            engine,
            executed_items,
            expected_count=len(candidates),
        )
        summary["import_coverage"] = import_coverage
        summary["import_execute"]["source_label_media_count"] = import_coverage.get("source_label_media_count")
        summary["import_execute"]["downstream_media_ids_count"] = import_coverage.get("downstream_media_ids_count", 0)
        summary["import_execute"]["downstream_identity_source"] = import_coverage.get("identity_source")
        if import_coverage["status"] != "passed":
            summary["status"] = import_coverage["status"]
            summary["success"] = False
            return summary

        prior_classification = (
            existing_details.get("public_summary_preview", {}).get("classification")
            if isinstance(existing_details.get("public_summary_preview"), dict)
            else None
        )
        if (
            import_result["status"] == "resumed_after_prior_successful_import"
        ):
            try:
                classification = resolve_classification_resume(imported_media_ids, prior_classification)
            except PhaseI7Error as exc:
                summary["classification"] = {
                    "status": "blocked_resume_identity_unverified",
                    "error": sanitize_public_text(str(exc))[:500],
                    "processed": 0,
                    "failed": 0,
                    "identity_required": True,
                }
                summary["status"] = "blocked_classification_resume_identity_unverified"
                summary["success"] = False
                return summary
        else:
            classification = run_classification(imported_media_ids, args.classification_chunk_size)
            if classification.get("status") == "completed":
                proof = media_ids_identity_proof(imported_media_ids)
                proof["identity_source"] = "db_source_label_import_coverage"
                classification["identity_proof"] = proof
        summary["classification"] = classification
        if classification["status"] != "completed":
            summary["status"] = "blocked_classification_failed"
            summary["success"] = False
            return summary

        ai_eligible_ids, ai_scope = select_i7_ai_eligible_media_ids(imported_media_ids)
        summary["ai_scope"] = ai_scope
        ai_result = run_ai_tagging(ai_eligible_ids, args.ai_chunk_size)
        ai_result.update(ai_scope)
        summary["ai_tagging"] = ai_result
        if ai_result["status"] not in {"completed", "noop_no_eligible_media"}:
            summary["status"] = "blocked_ai_tagging_failed"
            summary["success"] = False
            return summary

        localization = run_localization(limit=args.localization_limit, lang=args.lang)
        summary["localization"] = localization
        if localization["status"] not in {"completed", "noop_no_candidates"}:
            summary["status"] = "completed_with_localization_failures"
            summary["success"] = False
        else:
            summary["status"] = "completed"
            summary["success"] = True

        summary["db_counts_after"] = database_counts(engine, SOURCE_LABEL)
        db_storage_validation = validate_imported_db_and_storage(engine, context, imported_media_ids)
        if not apply_db_storage_validation_gate(summary, db_storage_validation):
            summary["finished_at"] = utc_now()
            return summary
        summary["import_item_ledger"] = write_import_item_ledger(
            args.ledger,
            downstream_import_items,
            args.import_ledger,
            downstream={
                "import_coverage": import_coverage,
                "classification": classification,
                "ai_scope": ai_scope,
                "ai_tagging": ai_result,
                "localization": localization,
            },
        )
        summary["finished_at"] = utc_now()
        return summary
    finally:
        engine.dispose()
        local_details["public_summary_preview"] = {
            key: summary.get(key)
            for key in [
                "status",
                "success",
                "i6_item_ledger_validation",
                "import_dry_run",
                "import_execute",
                "import_coverage",
                "classification",
                "ai_tagging",
                "localization",
            ]
        }
        write_json(args.validation_details, local_details)


def write_public_outputs(summary: dict[str, Any], report_md: Path, summary_json: Path) -> None:
    report_md.parent.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)

    summary["privacy_scan"] = {"public_report_leaks": []}
    summary_leaks = scan_privacy_leaks(summary)
    if summary_leaks:
        safe_summary = build_safe_privacy_blocked_summary(summary, summary_leaks=summary_leaks)
        report_md.write_text(render_safe_privacy_blocked_report(safe_summary), encoding="utf-8")
        write_json(summary_json, safe_summary)
        summary.clear()
        summary.update(safe_summary)
        return

    text = render_markdown_report(summary)
    markdown_leaks = scan_privacy_leaks(text)
    if markdown_leaks:
        safe_summary = build_safe_privacy_blocked_summary(summary, markdown_leaks=markdown_leaks)
        report_md.write_text(render_safe_privacy_blocked_report(safe_summary), encoding="utf-8")
        write_json(summary_json, safe_summary)
        summary.clear()
        summary.update(safe_summary)
        return

    report_md.write_text(text, encoding="utf-8")
    write_json(summary_json, summary)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--i6-details", type=Path, default=DEFAULT_I6_DETAILS)
    parser.add_argument("--import-manifest", type=Path, default=DEFAULT_IMPORT_MANIFEST)
    parser.add_argument("--import-ledger", type=Path, default=DEFAULT_IMPORT_LEDGER)
    parser.add_argument("--validation-details", type=Path, default=DEFAULT_VALIDATION_DETAILS)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_REPORT_MD)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    parser.add_argument("--db-backup-file", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-import", default="")
    parser.add_argument("--classification-chunk-size", type=int, default=100)
    parser.add_argument("--ai-chunk-size", type=int, default=200)
    parser.add_argument("--localization-limit", type=int)
    parser.add_argument("--localization-continuation-only", action="store_true")
    parser.add_argument("--max-additional-localization-candidates", type=int, default=500)
    parser.add_argument("--lang", default="zh-CN")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.localization_continuation_only:
            summary = run_localization_continuation(args)
        else:
            summary = run_pipeline(args)
    except Exception as exc:
        summary = build_base_summary(args)
        summary["status"] = "blocked_exception"
        summary["success"] = False
        summary["error"] = sanitize_public_text(f"{exc.__class__.__name__}: {exc}")[:1000]
        summary["finished_at"] = utc_now()
    write_public_outputs(summary, args.report_md, args.summary_json)
    sys.stdout.write(json.dumps({"status": summary["status"], "success": summary["success"]}, ensure_ascii=False) + "\n")
    return 0 if summary.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
