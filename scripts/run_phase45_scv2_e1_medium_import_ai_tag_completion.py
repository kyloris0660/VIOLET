#!/usr/bin/env python3
"""Phase 4.5-SCV2-E1 medium import and AI tag completion runner.

This is a phase-scoped operational runner.  It keeps source roots read-only,
copies only selected hydrated/readable files into app-managed storage, runs
existing classification and AI tagging jobs only for newly imported media, and
writes item-level private ledgers plus public-safe aggregate reports.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import multiprocessing
import os
import re
import shutil
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable, Mapping, Sequence

from dotenv import load_dotenv
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import Connection, Engine

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts import run_phase45_scv2_p0_controlled_medium_expansion_policy as p0  # noqa: E402

PHASE = "4.5-SCV2-E1"
PHASE_TITLE = "Medium Import + AI Tag Completion"
PHASE_SLUG = "phase-4.5-scv2-e1-medium-import-ai-tag-completion"
SOURCE_LABEL = "violet:phase4.5-scv2-e1:medium-import"
TRIGGER_SOURCE = "phase45-scv2-e1"
CONFIRM_PHRASE = "EXECUTE_PHASE45_SCV2_E1_MEDIUM_IMPORT_AI_TAG_COMPLETION"

DEFAULT_OUTPUT_DIR = ROOT / ".local_manifests" / PHASE_SLUG
PUBLIC_REPORT_MD = ROOT / "docs" / "reports" / f"{PHASE_SLUG}.md"
PUBLIC_REPORT_JSON = ROOT / "docs" / "reports" / f"{PHASE_SLUG}-summary.json"

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
TARGET_SUCCESSFUL_IMPORTS = 1761
MIN_SUCCESSFUL_IMPORTS = 1511
MAX_SUCCESSFUL_IMPORTS = 2011
OVER_SELECTION_RATIO = 1.35
DEFAULT_CANDIDATE_TARGET = math.ceil(TARGET_SUCCESSFUL_IMPORTS * OVER_SELECTION_RATIO)
DEFAULT_MAX_DISCOVERY_FILES = 10000
DEFAULT_HASH_TIMEOUT_SECONDS = 30
DEFAULT_COPY_TIMEOUT_SECONDS = 120

PRIVATE_ARTIFACT_NAMES = [
    "db-identity-before.json",
    "db-identity-after.json",
    "storage-identity.json",
    "source-root-inventory.json",
    "candidate-discovery-ledger.jsonl",
    "candidate-summary.json",
    "duplicate-detection-ledger.jsonl",
    "import-item-ledger.jsonl",
    "classification-ledger.jsonl",
    "ai-tagging-ledger.jsonl",
    "ai-tagging-failure-ledger.jsonl",
    "mutation-proof-before.json",
    "mutation-proof-after.json",
    "mutation-proof-delta.json",
    "safety-stop-conditions.json",
    "public-redaction-check.txt",
]

SUMMARY_REQUIRED_FIELDS = {
    "phase",
    "title",
    "branch",
    "generated_at",
    "baseline_before",
    "db_identity_before",
    "db_identity_after",
    "storage_identity",
    "source_root_safety",
    "candidate_discovery",
    "duplicate_detection",
    "import_results",
    "classification_results",
    "ai_tagging_results",
    "mutation_proof",
    "failure_budget",
    "public_redaction",
    "decision_matrix",
    "recommended_next_phase",
    "validation",
    "safety",
    "artifact_lifecycle",
    "private_artifacts",
}

CANDIDATE_LEDGER_REQUIRED_FIELDS = {
    "run_id",
    "candidate_id",
    "source_root_label",
    "source_locator_private_ref",
    "original_filename_sha256",
    "extension",
    "size",
    "detected_pixiv_ids",
    "candidate_source_reason",
    "cloud_state",
    "readable_status",
    "unsupported_reason",
    "duplicate_check_status",
    "existing_media_match",
    "eligible_for_import",
    "deferred_reason",
    "public_safe_label",
}

IMPORT_LEDGER_REQUIRED_FIELDS = {
    "run_id",
    "candidate_id",
    "public_safe_label",
    "status",
    "media_id",
    "duplicate_of_media_id",
    "failure_reason",
    "bytes_copied",
    "eligible_for_db_import",
}

AI_LEDGER_REQUIRED_FIELDS = {
    "run_id",
    "media_id",
    "ai_tag_attempted",
    "ai_tag_success",
    "failure_reason",
    "job_id",
    "output_tag_count",
    "has_ai_tag_provenance",
}

ALLOWED_TABLES = {
    "blombooru_media",
    "blombooru_media_tags",
    "blombooru_tags",
    "blombooru_ai_tag_jobs",
    "blombooru_classification_jobs",
    "blombooru_scan_jobs",
    "blombooru_scan_job_media",
}

FORBIDDEN_TABLES = {
    "blombooru_entities",
    "blombooru_entity_aliases",
    "blombooru_entity_external_identities",
    "blombooru_entity_evidence",
    "blombooru_media_entity_candidates",
    "blombooru_media_entity_assignments",
    "blombooru_entity_translations",
    "blombooru_external_sources",
    "blombooru_provider_cache",
    "blombooru_negative_lookup_cache",
    "blombooru_source_metadata_records",
    "blombooru_source_tag_observations",
    "blombooru_source_tag_registry",
    "blombooru_source_name_observations",
    "blombooru_source_name_registry",
    "blombooru_source_name_alias_candidates",
    "blombooru_source_metadata_evidence",
    "blombooru_source_searchable_name_assertions",
    "blombooru_source_name_candidate_extraction_runs",
    "blombooru_source_name_candidate_record_verdicts",
    "blombooru_source_name_candidates",
    "blombooru_source_concept_resolution_runs",
    "blombooru_source_concept_signals",
    "blombooru_source_concepts",
    "blombooru_source_concept_aliases",
    "blombooru_source_concept_evidence",
    "blombooru_source_concept_signal_links",
    "blombooru_source_concept_search_index",
    "blombooru_provider_cache",
    "blombooru_tag_translations",
    "blombooru_tag_translation_jobs",
    "blombooru_external_tag_category_lookup_cache",
    "blombooru_pixiv_tag_taxonomy_kb",
    "blombooru_pixiv_tag_alias_kb",
}

PHASE_BOUNDARY = {
    "provider_calls": False,
    "pixiv": False,
    "gallery_dl": False,
    "source_metadata_extraction": False,
    "source_concept_resolver": False,
    "entity_bridge": False,
    "localization": False,
    "llm": False,
    "browser_validation": False,
    "server_start": False,
}

WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"(?i)(?<![A-Z0-9_])[A-Z]:[\\/](?:(?![\r\n\"<>|]).)+")
UNC_PATH_RE = re.compile(r"\\\\(?:(?![\r\n\"<>|]).)+")
FILE_URI_RE = re.compile(r"file://[^\s\"'<>]+", re.IGNORECASE)
POSIX_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])/(?:Users|home|root|etc|usr|private|var|Volumes|workspace|mnt|tmp|opt|srv|data)"
    r"(?:(?![\r\n\"<>|]).)+"
)
IMAGE_FILENAME_RE = re.compile(r"\b[^\\/\s\"'<>]{2,}\.(?:jpe?g|png|webp|gif)\b", re.IGNORECASE)
SECRET_RE = re.compile(r"(?:Bearer\s+[A-Za-z0-9._~+\-/]+=*|(?:sk|key)[-_][A-Za-z0-9_\-]{8,})", re.IGNORECASE)
PIXIV_ID_RE = re.compile(r"(?<!\d)(?P<work_id>\d{5,12})(?:[_-]p(?P<page_index>\d+))?(?!\d)", re.IGNORECASE)


class E1BlockedError(RuntimeError):
    """Raised when a hard E1 safety gate fails."""


@dataclass
class RuntimeContext:
    run_id: str
    mode: str
    output_dir: Path
    storage_root: Path
    original_dir: Path
    thumbnail_dir: Path
    database_url_safe: str
    db_identity_source: dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    if hasattr(value, "value"):
        return value.value
    return str(value)


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=json_default) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=json_default) + "\n")
            count += 1
    return count


def stable_private_id(value: Any, prefix: str = "item") -> str:
    digest = hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def hash_text(value: Any) -> str:
    return hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()


def candidate_target_count(target_successful_imports: int, over_selection_ratio: float) -> int:
    return int(math.ceil(target_successful_imports * over_selection_ratio))


def extract_pixiv_ids(value: Any) -> list[dict[str, Any]]:
    text_value = str(value or "")
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for match in PIXIV_ID_RE.finditer(text_value):
        work_id = match.group("work_id")
        if len(work_id) < 7:
            continue
        page_index = int(match.group("page_index") or 0)
        key = (work_id, page_index)
        if key in seen:
            continue
        seen.add(key)
        found.append({"work_id": work_id, "page_index": page_index})
    return found


def pixiv_key_from_ids(ids: Sequence[Mapping[str, Any]]) -> tuple[str, int] | None:
    if not ids:
        return None
    first = ids[0]
    work_id = str(first.get("work_id") or "")
    if not work_id:
        return None
    return work_id, int(first.get("page_index") or 0)


def candidate_priority_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    gate_allowed = bool(candidate.get("source_gate_allowed", candidate.get("eligible_for_import", False)))
    pixiv_like = bool(candidate.get("detected_pixiv_ids"))
    extension = str(candidate.get("extension") or "").lower()
    ext_rank = {".jpg": 0, ".jpeg": 0, ".png": 1, ".webp": 2, ".gif": 3}.get(extension, 9)
    size = int(candidate.get("size") or 0)
    locator = str(candidate.get("source_locator_private_ref") or candidate.get("public_safe_label") or "")
    return (
        0 if gate_allowed else 1,
        0 if pixiv_like else 1,
        ext_rank,
        0 if size > 0 else 1,
        locator.lower(),
    )


def classify_duplicate(candidate: Mapping[str, Any], existing: Mapping[str, Any], seen_hashes: set[str]) -> dict[str, Any]:
    file_hash = str(candidate.get("file_hash") or "")
    filename_key = str(candidate.get("filename_size_key") or "")
    pixiv_key = candidate.get("pixiv_key")

    if file_hash and file_hash in existing.get("hash_to_media", {}):
        return {
            "status": "duplicate",
            "reason": "duplicate_by_hash",
            "duplicate_of_media_id": existing["hash_to_media"][file_hash],
        }
    if file_hash and file_hash in seen_hashes:
        return {"status": "duplicate", "reason": "duplicate_by_manifest_hash", "duplicate_of_media_id": None}
    if pixiv_key and pixiv_key in existing.get("pixiv_key_to_media", {}):
        return {
            "status": "duplicate",
            "reason": "duplicate_by_pixiv_id_page",
            "duplicate_of_media_id": existing["pixiv_key_to_media"][pixiv_key],
        }
    if filename_key and filename_key in existing.get("filename_size_to_media", {}):
        return {
            "status": "duplicate",
            "reason": "duplicate_by_filename_size",
            "duplicate_of_media_id": existing["filename_size_to_media"][filename_key],
        }
    return {"status": "unique", "reason": "not_duplicate", "duplicate_of_media_id": None}


def evaluate_failure_budget(
    *,
    attempted: int,
    failure_reasons: Mapping[str, int],
    consecutive_failures: int,
    max_item_failures: int,
    max_failure_rate: float,
    max_same_reason_failures: int,
    max_consecutive_failures: int,
) -> dict[str, Any]:
    failed = sum(int(v) for v in failure_reasons.values())
    failure_rate = (failed / attempted) if attempted else 0.0
    same_reason_exceeded = {
        reason: count for reason, count in failure_reasons.items() if int(count) > max_same_reason_failures
    }
    exceeded = []
    if failed > max_item_failures:
        exceeded.append("max_item_failures")
    if attempted and failure_rate > max_failure_rate:
        exceeded.append("max_failure_rate")
    if same_reason_exceeded:
        exceeded.append("max_same_reason_failures")
    if consecutive_failures > max_consecutive_failures:
        exceeded.append("max_consecutive_failures")
    return {
        "attempted": attempted,
        "failed": failed,
        "failure_rate": round(failure_rate, 6),
        "failure_reasons": dict(sorted(failure_reasons.items())),
        "consecutive_failures": consecutive_failures,
        "max_item_failures": max_item_failures,
        "max_failure_rate": max_failure_rate,
        "max_same_reason_failures": max_same_reason_failures,
        "max_consecutive_failures": max_consecutive_failures,
        "same_reason_exceeded": same_reason_exceeded,
        "exceeded": exceeded,
        "passed": not exceeded,
    }


def classify_table_mutations(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    allowed_tables: set[str] | None = None,
    forbidden_tables: set[str] | None = None,
) -> dict[str, Any]:
    allowed_tables = allowed_tables or ALLOWED_TABLES
    forbidden_tables = forbidden_tables or FORBIDDEN_TABLES
    changed = []
    before_tables = before.get("tables", {})
    after_tables = after.get("tables", {})
    for table in sorted(set(before_tables) | set(after_tables)):
        left = before_tables.get(table, {}).get("count")
        right = after_tables.get(table, {}).get("count")
        left_status = before_tables.get(table, {}).get("status")
        right_status = after_tables.get(table, {}).get("status")
        if left_status == "missing_table" or right_status == "missing_table":
            continue
        if left != right:
            changed.append({"table": table, "before": left, "after": right, "delta": (right or 0) - (left or 0)})
    expected = [row for row in changed if row["table"] in allowed_tables]
    forbidden = [row for row in changed if row["table"] in forbidden_tables]
    unexpected = [row for row in changed if row["table"] not in allowed_tables and row["table"] not in forbidden_tables]
    missing = sorted(table for table in forbidden_tables if before_tables.get(table, {}).get("status") == "missing_table")
    return {
        "changed_tables": changed,
        "expected_changed_tables": expected,
        "forbidden_changed_tables": forbidden,
        "unexpected_changed_tables": unexpected,
        "missing_forbidden_tables": missing,
        "passed": not forbidden and not unexpected,
        "allowed_tables": sorted(allowed_tables),
        "forbidden_tables": sorted(forbidden_tables),
    }


def ai_tag_continuity_result(eligible_new_media: int, tagged_eligible_new_media: int, failures: int = 0) -> dict[str, Any]:
    ratio = (tagged_eligible_new_media / eligible_new_media) if eligible_new_media else 1.0
    missing = max(0, eligible_new_media - tagged_eligible_new_media)
    return {
        "eligible_new_media": eligible_new_media,
        "eligible_new_media_with_ai_tag_provenance": tagged_eligible_new_media,
        "eligible_new_media_without_ai_tag_provenance": missing,
        "ai_tag_failures": failures,
        "coverage_ratio": round(ratio, 6),
        "coverage_pct": round(ratio * 100, 2),
        "target_pct": 100.0,
        "passed": missing == failures and ratio == 1.0,
    }


def scan_public_text(value: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    patterns = [
        ("windows_absolute_path", WINDOWS_ABSOLUTE_PATH_RE),
        ("unc_path", UNC_PATH_RE),
        ("file_uri", FILE_URI_RE),
        ("posix_absolute_path", POSIX_ABSOLUTE_PATH_RE),
        ("secret_token", SECRET_RE),
        ("image_filename", IMAGE_FILENAME_RE),
    ]
    for reason, pattern in patterns:
        if pattern.search(value):
            findings.append({"reason": reason, "sample": pattern.search(value).group(0)[:80]})
    return findings


def scan_public_artifacts(paths: Sequence[Path]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    for path in paths:
        if not path.exists():
            findings.append({"path": path.name, "reason": "missing_public_artifact", "sample": ""})
            continue
        for finding in scan_public_text(path.read_text(encoding="utf-8")):
            findings.append({"path": path.name, **finding})
    return {"checked_paths": [path.name for path in paths], "findings": findings, "passed": not findings}


def validate_summary_schema(summary: Mapping[str, Any]) -> dict[str, Any]:
    missing = sorted(SUMMARY_REQUIRED_FIELDS - set(summary))
    return {"required_fields": sorted(SUMMARY_REQUIRED_FIELDS), "missing_fields": missing, "passed": not missing}


def phase_boundary_status() -> dict[str, Any]:
    return dict(PHASE_BOUNDARY)


def apply_phase_env_overrides() -> dict[str, str]:
    overrides = {
        "AI_TAGGING_ENABLED": "true",
        "AI_AUTO_TAG_AFTER_IMPORT": "false",
        "AI_TAGGING_AUTO_LOCALIZATION": "false",
        "TAG_TRANSLATION_AUTO_ENABLED": "false",
        "TAG_TRANSLATION_BACKGROUND_ENABLED": "false",
        "TAG_TRANSLATION_LLM_ENABLED": "false",
        "CONTENT_CLASSIFICATION_ENABLED": "true",
        "ENTITY_ALIAS_RESOLVER_ENABLED": "false",
        "HF_HUB_OFFLINE": "1",
    }
    os.environ.update(overrides)
    return overrides


def git_value(args: Sequence[str]) -> str:
    import subprocess

    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT).strip()


def create_engine_for_phase() -> tuple[Engine, dict[str, Any]]:
    load_dotenv(ROOT / ".env", override=False)
    database_url, identity = p0.build_database_url()
    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10, "options": "-c statement_timeout=300000"},
    )
    return engine, identity


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_exists(conn: Connection, table_name: str) -> bool:
    return bool(conn.execute(text("SELECT to_regclass(:name) IS NOT NULL"), {"name": table_name}).scalar())


def count_table(conn: Connection, table_name: str) -> dict[str, Any]:
    if not table_exists(conn, table_name):
        return {"status": "missing_table", "count": None}
    value = int(conn.execute(text(f"SELECT COUNT(*) FROM {qident(table_name)}")).scalar() or 0)
    return {"status": "present", "count": value}


def list_blombooru_tables(conn: Connection) -> list[str]:
    rows = conn.execute(
        text(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_type = 'BASE TABLE'
              AND table_name LIKE 'blombooru_%'
            ORDER BY table_name
            """
        )
    ).scalars()
    return [str(row) for row in rows]


def build_table_counts(conn: Connection) -> dict[str, Any]:
    tables = sorted(set(list_blombooru_tables(conn)) | ALLOWED_TABLES | FORBIDDEN_TABLES)
    return {"recorded_at": utc_now(), "tables": {table: count_table(conn, table) for table in tables}}


def scalar_count(conn: Connection, sql: str, params: Mapping[str, Any] | None = None) -> int:
    return int(conn.execute(text(sql), params or {}).scalar() or 0)


def build_baseline(conn: Connection) -> dict[str, Any]:
    baseline = p0.audit_current_media_baseline(conn)
    return {
        "total_media": baseline.get("total_media"),
        "eligible_media": baseline.get("eligible_media"),
        "eligible_policy": baseline.get("eligible_policy"),
        "eligible_media_with_ai_tag_provenance": baseline.get("eligible_media_with_ai_tag_provenance"),
        "eligible_media_without_ai_tag_provenance": baseline.get("eligible_media_without_ai_tag_provenance"),
        "eligible_ai_tag_provenance_pct": baseline.get("eligible_ai_tag_provenance_pct"),
        "media_with_source_metadata": baseline.get("media_with_source_metadata"),
        "source_metadata_total_rows": baseline.get("source_metadata_total_rows"),
        "content_class_distribution": baseline.get("content_class_distribution"),
    }


def public_db_identity(identity: Mapping[str, Any], conn: Connection) -> dict[str, Any]:
    current = conn.execute(text("SELECT current_database(), current_user")).one()
    resolution = dict(identity.get("db_resolution") or {})
    return {
        "recorded_at": utc_now(),
        "host": identity.get("host"),
        "port": identity.get("port"),
        "database": identity.get("database"),
        "connected_database": current[0],
        "connected_user_recorded": bool(current[1]),
        "password_value_recorded": False,
        "db_resolution": {
            "app_compatible": resolution.get("app_compatible"),
            "runner_matches_app_equivalent": resolution.get("runner_matches_app_equivalent"),
            "urls_match": resolution.get("urls_match"),
            "field_sources": resolution.get("field_sources"),
            "password_present": resolution.get("password_present"),
            "password_value_recorded": False,
        },
        "violet_env": os.getenv("VIOLET_ENV", "development") or "development",
        "git_branch": git_value(["branch", "--show-current"]),
        "git_sha": git_value(["rev-parse", "HEAD"]),
        "python_executable": str(Path(sys.executable)),
        "python_executable_path_recorded_private_only": True,
    }


def assert_db_preflight(identity: Mapping[str, Any], public_identity: Mapping[str, Any]) -> None:
    db_name = str(public_identity.get("connected_database") or public_identity.get("database") or "")
    violet_env = str(public_identity.get("violet_env") or "development").lower()
    host = str(public_identity.get("host") or "")
    if db_name != "blombooru":
        raise E1BlockedError(f"wrong_db:{db_name}")
    if violet_env not in {"development", ""}:
        raise E1BlockedError(f"wrong_violet_env:{violet_env}")
    if host not in {"localhost", "127.0.0.1", ""}:
        raise E1BlockedError(f"wrong_db_host:{host}")
    if identity.get("password"):
        raise E1BlockedError("db_identity_contains_password_value")


def env_local_library_paths() -> list[Path]:
    load_dotenv(ROOT / ".env", override=False)
    raw = os.getenv("LOCAL_LIBRARY_PATHS", "")
    return [Path(part.strip()).expanduser() for part in raw.split("|") if part.strip()]


def source_roots_from_local_manifests() -> list[Path]:
    paths: list[Path] = []
    details_path = ROOT / ".local_manifests" / "phase-3.8d-i6-staging-copy-retry-details.json"
    if details_path.exists():
        try:
            data = json.loads(details_path.read_text(encoding="utf-8"))
            for raw in data.get("source_roots") or []:
                if raw:
                    paths.append(Path(str(raw)).expanduser())
        except Exception:
            pass
    return paths


def unique_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        try:
            resolved = str(path.resolve())
        except Exception:
            resolved = str(path)
        key = resolved.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(Path(resolved))
    return result


def path_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def is_icloud_like(path: Path) -> bool:
    text_value = str(path).replace("\\", "/").lower()
    return "icloud" in text_value or "cloud" in text_value


def build_storage_identity(source_roots: Sequence[Path], storage_root: Path) -> dict[str, Any]:
    original_dir = storage_root / "media" / "original"
    thumbnail_dir = storage_root / "media" / "thumbnails"
    overlaps = []
    for idx, source_root in enumerate(source_roots, start=1):
        exists = source_root.exists()
        source_under_storage = exists and path_under(source_root, storage_root)
        storage_under_source = exists and path_under(storage_root, source_root)
        if source_under_storage or storage_under_source:
            overlaps.append(f"source_root_{idx}")
    return {
        "recorded_at": utc_now(),
        "storage_root_private": str(storage_root),
        "storage_root_label": "app_storage",
        "storage_root_exists": storage_root.exists(),
        "storage_root_under_repo": path_under(storage_root, ROOT),
        "original_path_private": str(original_dir),
        "original_path_label": "app_storage/media/original",
        "original_path_exists": original_dir.exists(),
        "thumbnails_path_private": str(thumbnail_dir),
        "thumbnails_path_label": "app_storage/media/thumbnails",
        "thumbnails_path_exists": thumbnail_dir.exists(),
        "source_storage_overlap_labels": overlaps,
        "source_storage_overlap_safe": not overlaps,
        "paths_private_artifact_only": True,
    }


def build_source_root_inventory(source_roots: Sequence[Path], storage_root: Path) -> list[dict[str, Any]]:
    inventory = []
    for idx, source_root in enumerate(source_roots, start=1):
        exists = source_root.exists()
        is_dir = exists and source_root.is_dir()
        inventory.append(
            {
                "source_root_label": f"source_root_{idx}",
                "source_root_private": str(source_root),
                "private_root_sha256": hash_text(source_root),
                "exists": exists,
                "is_dir": is_dir,
                "protected_root": True,
                "read_only_from_e1_perspective": True,
                "icloud_or_cloud_backed_hint": is_icloud_like(source_root),
                "under_app_storage": exists and path_under(source_root, storage_root),
                "app_storage_under_source_root": exists and path_under(storage_root, source_root),
                "source_reason": "configured" if source_root in env_local_library_paths() else "previous_local_manifest",
            }
        )
    return inventory


def assert_storage_preflight(storage: Mapping[str, Any], source_inventory: Sequence[Mapping[str, Any]]) -> None:
    if not storage.get("storage_root_exists"):
        raise E1BlockedError("storage_root_missing")
    if storage.get("source_storage_overlap_labels"):
        raise E1BlockedError("unsafe_source_storage_overlap")
    safe_roots = [root for root in source_inventory if root.get("exists") and root.get("is_dir") and not root.get("under_app_storage")]
    if not safe_roots:
        raise E1BlockedError("no_safe_source_root")


def load_runtime_context(args: argparse.Namespace, engine_identity: Mapping[str, Any]) -> RuntimeContext:
    storage_root = Path(os.getenv("VIOLET_STORAGE_ROOT", "") or ROOT).expanduser().resolve()
    return RuntimeContext(
        run_id=args.run_id or f"e1-{uuid.uuid4()}",
        mode="execute" if args.execute else "dry_run",
        output_dir=args.output_dir,
        storage_root=storage_root,
        original_dir=storage_root / "media" / "original",
        thumbnail_dir=storage_root / "media" / "thumbnails",
        database_url_safe=str(engine_identity.get("safe_url") or ""),
        db_identity_source=dict(engine_identity),
    )


def safe_candidate_label(index: int, extension: str) -> str:
    ext = extension.lower() if extension.lower() in SUPPORTED_EXTENSIONS else ".bin"
    return f"candidate_{index:06d}{ext}"


def _source_gate_for_path(path: Path, safe_label: str) -> dict[str, Any]:
    from app.services.source_ingestion_gate import SourceIngestionGate

    gate = SourceIngestionGate.evaluate_path_source(path, safe_label=safe_label, hydration_policy_enabled=False)
    return gate.to_public_dict()


def discover_candidates(
    *,
    run_id: str,
    source_roots: Sequence[Path],
    storage_root: Path,
    max_discovery_files: int,
    max_file_size_mb: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    extension_counts: Counter[str] = Counter()
    deferred_counts: Counter[str] = Counter()
    index = 0
    max_bytes = max_file_size_mb * 1024 * 1024
    for root_index, source_root in enumerate(source_roots, start=1):
        if len(rows) >= max_discovery_files:
            break
        if not source_root.exists() or not source_root.is_dir():
            continue
        try:
            entries = source_root.rglob("*")
        except OSError:
            deferred_counts["source_root_walk_failed"] += 1
            continue
        for path in entries:
            if len(rows) >= max_discovery_files:
                break
            try:
                if not path.is_file():
                    continue
            except OSError:
                deferred_counts["stat_failed"] += 1
                continue
            extension = path.suffix.lower()
            if extension not in SUPPORTED_EXTENSIONS:
                extension_counts[extension or "<none>"] += 1
                continue
            index += 1
            public_label = safe_candidate_label(index, extension)
            candidate_id = stable_private_id(path, "candidate")
            unsupported_reason = None
            deferred_reason = None
            size = 0
            try:
                stat = path.stat()
                size = int(stat.st_size)
            except OSError:
                deferred_reason = "stat_failed"
            if size <= 0 and deferred_reason is None:
                deferred_reason = "zero_byte"
            if size > max_bytes and deferred_reason is None:
                deferred_reason = "too_large"
            path_escape = not path_under(path, source_root)
            if path_escape and deferred_reason is None:
                deferred_reason = "path_escape"
            gate = _source_gate_for_path(path, public_label)
            if gate.get("blocked") and deferred_reason is None:
                deferred_reason = str(gate.get("reason") or "source_gate_blocked")
            detected_pixiv_ids = extract_pixiv_ids(path.name)
            extension_counts[extension] += 1
            row = {
                "run_id": run_id,
                "candidate_id": candidate_id,
                "source_root_label": f"source_root_{root_index}",
                "source_locator_private_ref": str(path),
                "original_filename_sha256": hash_text(path.name),
                "original_filename_redacted": True,
                "extension": extension,
                "size": size,
                "detected_pixiv_ids": detected_pixiv_ids,
                "candidate_source_reason": "configured_or_previous_source_root_supported_extension",
                "cloud_state": gate.get("cloud_state"),
                "source_gate_allowed": bool(gate.get("allowed")),
                "readable_status": "not_read_yet" if not deferred_reason else "not_read_deferred",
                "unsupported_reason": unsupported_reason,
                "duplicate_check_status": "not_checked",
                "existing_media_match": None,
                "eligible_for_import": deferred_reason is None,
                "deferred_reason": deferred_reason,
                "public_safe_label": public_label,
                "filename_size_key": f"{path.name.lower()}:{size}",
                "pixiv_key": pixiv_key_from_ids(detected_pixiv_ids),
            }
            if deferred_reason:
                deferred_counts[deferred_reason] += 1
            rows.append(row)
    rows.sort(key=candidate_priority_key)
    summary = {
        "total_candidates_considered": len(rows),
        "eligible_pre_duplicate": sum(1 for row in rows if row.get("eligible_for_import")),
        "deferred_pre_duplicate": sum(1 for row in rows if not row.get("eligible_for_import")),
        "extension_counts": dict(sorted(extension_counts.items())),
        "deferred_reason_counts": dict(sorted(deferred_counts.items())),
        "max_discovery_files": max_discovery_files,
        "paths_private_artifact_only": True,
    }
    return rows, summary


def _hash_worker(path: str, conn: Any) -> None:
    try:
        digest = hashlib.md5()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        conn.send({"ok": True, "hash": digest.hexdigest(), "error_reason": None, "error_message": None})
    except Exception as exc:
        conn.send(
            {
                "ok": False,
                "hash": None,
                "error_reason": classify_file_access_error_name(exc),
                "error_message": str(exc)[:500],
            }
        )
    finally:
        conn.close()


def _hash_one(path: str) -> dict[str, Any]:
    try:
        digest = hashlib.md5()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return {"ok": True, "hash": digest.hexdigest(), "error_reason": None, "error_message": None}
    except Exception as exc:
        return {
            "ok": False,
            "hash": None,
            "error_reason": classify_file_access_error_name(exc),
            "error_message": str(exc)[:500],
        }


def _hash_batch_worker(items: list[tuple[str, str]], conn: Any) -> None:
    try:
        for candidate_id, path in items:
            conn.send({"event": "started", "candidate_id": candidate_id})
            result = _hash_one(path)
            conn.send({"event": "result", "candidate_id": candidate_id, **result})
    finally:
        conn.close()


def classify_file_access_error_name(error: BaseException) -> str:
    winerror = getattr(error, "winerror", None)
    errno_value = getattr(error, "errno", None)
    if winerror in (2, 3):
        return "source_missing"
    if winerror == 5 or errno_value in {13, 1}:
        return "permission_denied"
    if winerror == 388:
        return "cloud_network_unavailable"
    return "unreadable_source"


def hash_file_with_timeout(path: Path, timeout_seconds: int) -> dict[str, Any]:
    parent_conn, child_conn = multiprocessing.Pipe(duplex=False)
    proc = multiprocessing.Process(target=_hash_worker, args=(str(path), child_conn), daemon=True)
    proc.start()
    child_conn.close()
    proc.join(timeout_seconds)
    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=2)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=2)
        parent_conn.close()
        return {"ok": False, "hash": None, "error_reason": "read_timeout", "error_message": f"hash timed out after {timeout_seconds}s"}
    if parent_conn.poll(timeout=2):
        result = parent_conn.recv()
        parent_conn.close()
        return result
    parent_conn.close()
    return {"ok": False, "hash": None, "error_reason": "read_no_result", "error_message": f"hash worker exited with {proc.exitcode}"}


def hash_candidates_with_timeout(rows: Sequence[Mapping[str, Any]], timeout_seconds: int) -> dict[str, dict[str, Any]]:
    """Hash candidates with one reusable worker and per-file timeout recovery."""

    if timeout_seconds <= 0:
        return {
            str(row["candidate_id"]): _hash_one(str(row["source_locator_private_ref"]))
            for row in rows
        }

    pending = [
        (str(row["candidate_id"]), str(row["source_locator_private_ref"]))
        for row in rows
    ]
    results: dict[str, dict[str, Any]] = {}
    while pending:
        parent_conn, child_conn = multiprocessing.Pipe(duplex=False)
        proc = multiprocessing.Process(target=_hash_batch_worker, args=(pending, child_conn), daemon=True)
        proc.start()
        child_conn.close()
        current_id: str | None = None
        current_started = 0.0
        made_progress = False
        timed_out = False
        pipe_failed = False
        while True:
            try:
                has_message = parent_conn.poll(timeout=0.25)
            except (BrokenPipeError, EOFError, OSError):
                pipe_failed = True
                break
            if not proc.is_alive() and not has_message:
                break
            if has_message:
                try:
                    msg = parent_conn.recv()
                except (BrokenPipeError, EOFError, OSError):
                    pipe_failed = True
                    break
                if msg.get("event") == "started":
                    current_id = str(msg["candidate_id"])
                    current_started = time.monotonic()
                elif msg.get("event") == "result":
                    candidate_id = str(msg["candidate_id"])
                    results[candidate_id] = {
                        "ok": bool(msg.get("ok")),
                        "hash": msg.get("hash"),
                        "error_reason": msg.get("error_reason"),
                        "error_message": msg.get("error_message"),
                    }
                    made_progress = True
                    if current_id == candidate_id:
                        current_id = None
            if current_id and time.monotonic() - current_started > timeout_seconds:
                results[current_id] = {
                    "ok": False,
                    "hash": None,
                    "error_reason": "read_timeout",
                    "error_message": f"hash timed out after {timeout_seconds}s",
                }
                timed_out = True
                break
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=2)
        parent_conn.close()
        if pipe_failed and not made_progress:
            for candidate_id, path in pending:
                results[candidate_id] = _hash_one(path)
            pending = []
            break
        pending = [(candidate_id, path) for candidate_id, path in pending if candidate_id not in results]
        if pending and not made_progress and not timed_out:
            candidate_id, _path = pending.pop(0)
            results[candidate_id] = {
                "ok": False,
                "hash": None,
                "error_reason": "read_no_result",
                "error_message": f"hash worker exited with {proc.exitcode}",
            }
    return results


def existing_media_index(conn: Connection) -> dict[str, Any]:
    rows = conn.execute(
        text("SELECT id, hash, filename, file_size FROM blombooru_media ORDER BY id ASC")
    ).mappings()
    hash_to_media: dict[str, int] = {}
    filename_size_to_media: dict[str, int] = {}
    pixiv_key_to_media: dict[tuple[str, int], int] = {}
    for row in rows:
        media_id = int(row["id"])
        file_hash = str(row.get("hash") or "")
        if file_hash and file_hash not in hash_to_media:
            hash_to_media[file_hash] = media_id
        filename = str(row.get("filename") or "")
        file_size = int(row.get("file_size") or 0)
        if filename and file_size:
            filename_size_to_media.setdefault(f"{filename.lower()}:{file_size}", media_id)
        pixiv_key = pixiv_key_from_ids(extract_pixiv_ids(filename))
        if pixiv_key:
            pixiv_key_to_media.setdefault(pixiv_key, media_id)
    return {
        "hash_to_media": hash_to_media,
        "filename_size_to_media": filename_size_to_media,
        "pixiv_key_to_media": pixiv_key_to_media,
    }


def run_duplicate_detection(
    conn: Connection,
    candidates: Sequence[dict[str, Any]],
    *,
    candidate_target: int,
    hash_timeout_seconds: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected = [dict(row) for row in candidates if row.get("eligible_for_import")][:candidate_target]
    existing = existing_media_index(conn)
    seen_hashes: set[str] = set()
    reason_counts: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    hashable_rows = [row for row in selected if row.get("source_gate_allowed")]
    hash_results = hash_candidates_with_timeout(hashable_rows, hash_timeout_seconds)
    for row in selected:
        path = Path(str(row["source_locator_private_ref"]))
        if not row.get("source_gate_allowed"):
            row.update(
                {
                    "duplicate_check_status": "not_checked_source_gate_blocked",
                    "eligible_for_import": False,
                    "deferred_reason": row.get("deferred_reason") or "source_gate_blocked",
                    "existing_media_match": None,
                }
            )
            reason_counts[str(row["deferred_reason"])] += 1
            rows.append(row)
            continue
        hash_result = hash_results.get(str(row["candidate_id"])) or {
            "ok": False,
            "hash": None,
            "error_reason": "read_no_result",
            "error_message": "missing hash result",
        }
        if not hash_result.get("ok"):
            reason = str(hash_result.get("error_reason") or "unreadable_source")
            row.update(
                {
                    "file_hash": None,
                    "duplicate_check_status": "not_checked_hash_failed",
                    "readable_status": "failed",
                    "eligible_for_import": False,
                    "deferred_reason": reason,
                    "hash_error": hash_result.get("error_message"),
                    "existing_media_match": None,
                }
            )
            reason_counts[reason] += 1
            rows.append(row)
            continue
        file_hash = str(hash_result["hash"])
        row["file_hash"] = file_hash
        row["readable_status"] = "hash_read_ok"
        duplicate = classify_duplicate(row, existing, seen_hashes)
        row["duplicate_check_status"] = duplicate["reason"]
        row["existing_media_match"] = duplicate.get("duplicate_of_media_id")
        if duplicate["status"] == "duplicate":
            row["eligible_for_import"] = False
            row["deferred_reason"] = duplicate["reason"]
            reason_counts[duplicate["reason"]] += 1
        else:
            row["eligible_for_import"] = True
            row["deferred_reason"] = None
            reason_counts["unique_import_candidate"] += 1
            seen_hashes.add(file_hash)
        rows.append(row)
    summary = {
        "selected_for_duplicate_detection": len(selected),
        "unique_import_candidates": reason_counts.get("unique_import_candidate", 0),
        "duplicate_count": sum(count for reason, count in reason_counts.items() if reason.startswith("duplicate_")),
        "deferred_or_failed_count": sum(
            count
            for reason, count in reason_counts.items()
            if reason != "unique_import_candidate" and not reason.startswith("duplicate_")
        ),
        "reason_counts": dict(sorted(reason_counts.items())),
        "hash_timeout_seconds": hash_timeout_seconds,
        "paths_private_artifact_only": True,
    }
    return rows, summary


def _copy_worker(source: str, target: str, conn: Any) -> None:
    try:
        shutil.copy2(source, target)
        conn.send({"ok": True, "bytes_copied": Path(target).stat().st_size, "error_reason": None, "error_message": None})
    except Exception as exc:
        conn.send(
            {
                "ok": False,
                "bytes_copied": 0,
                "error_reason": classify_file_access_error_name(exc),
                "error_message": str(exc)[:500],
            }
        )
    finally:
        conn.close()


def copy_with_timeout(source: Path, target_tmp: Path, timeout_seconds: int) -> dict[str, Any]:
    parent_conn, child_conn = multiprocessing.Pipe(duplex=False)
    proc = multiprocessing.Process(target=_copy_worker, args=(str(source), str(target_tmp), child_conn), daemon=True)
    proc.start()
    child_conn.close()
    proc.join(timeout_seconds)
    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=2)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=2)
        parent_conn.close()
        return {"ok": False, "bytes_copied": 0, "error_reason": "read_timeout", "error_message": f"copy timed out after {timeout_seconds}s"}
    if parent_conn.poll(timeout=2):
        result = parent_conn.recv()
        parent_conn.close()
        return result
    parent_conn.close()
    return {"ok": False, "bytes_copied": 0, "error_reason": "copy_no_result", "error_message": f"copy worker exited with {proc.exitcode}"}


def storage_relative(path: Path, storage_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(storage_root.resolve())).replace("\\", "/")
    except ValueError as exc:
        raise E1BlockedError("app_storage_path_escape") from exc


def ensure_app_dirs(context: RuntimeContext, execute: bool) -> None:
    if execute:
        context.original_dir.mkdir(parents=True, exist_ok=True)
        context.thumbnail_dir.mkdir(parents=True, exist_ok=True)
        return
    if not context.original_dir.exists() or not context.thumbnail_dir.exists():
        raise E1BlockedError("dry_run_storage_dirs_missing")


def insert_imported_media(
    conn: Connection,
    *,
    filename: str,
    managed_path: str,
    managed_thumbnail: str,
    metadata: Mapping[str, Any],
) -> int:
    from app.enums import RatingEnum
    from app.schemas import FileTypeEnum

    file_type = metadata.get("file_type")
    if isinstance(file_type, FileTypeEnum):
        file_type_value = file_type.value
    elif hasattr(file_type, "value"):
        file_type_value = file_type.value
    else:
        file_type_value = str(file_type)
    result = conn.execute(
        text(
            """
            INSERT INTO blombooru_media
            (filename, path, thumbnail_path, hash, file_type, mime_type,
             file_size, width, height, duration, rating, is_shared,
             share_ai_metadata, content_class_locked, content_class_reviewed, source)
            VALUES (:filename, :path, :thumbnail_path, :hash, :file_type, :mime_type,
                    :file_size, :width, :height, :duration, :rating, :is_shared,
                    :share_ai_metadata, :content_class_locked, :content_class_reviewed, :source)
            RETURNING id
            """
        ),
        {
            "filename": filename,
            "path": managed_path,
            "thumbnail_path": managed_thumbnail,
            "hash": metadata.get("hash"),
            "file_type": file_type_value,
            "mime_type": metadata.get("mime_type"),
            "file_size": metadata.get("file_size"),
            "width": metadata.get("width"),
            "height": metadata.get("height"),
            "duration": metadata.get("duration"),
            "rating": RatingEnum.safe.value,
            "is_shared": False,
            "share_ai_metadata": False,
            "content_class_locked": False,
            "content_class_reviewed": False,
            "source": SOURCE_LABEL,
        },
    )
    return int(result.scalar_one())


def execute_imports(
    engine: Engine,
    context: RuntimeContext,
    duplicate_rows: Sequence[dict[str, Any]],
    *,
    execute: bool,
    target_successful_imports: int,
    min_successful_imports: int,
    max_successful_imports: int,
    copy_timeout_seconds: int,
    failure_budget: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[int]]:
    ensure_app_dirs(context, execute)
    candidates = [row for row in duplicate_rows if row.get("eligible_for_import")]
    if len(candidates) < min_successful_imports and execute:
        raise E1BlockedError(f"not_enough_unique_candidates:{len(candidates)}")
    if not execute:
        ledger = [
            {
                "run_id": context.run_id,
                "candidate_id": row["candidate_id"],
                "public_safe_label": row["public_safe_label"],
                "status": "would_import",
                "media_id": None,
                "duplicate_of_media_id": None,
                "failure_reason": None,
                "bytes_copied": 0,
                "eligible_for_db_import": True,
            }
            for row in candidates[:target_successful_imports]
        ]
        return ledger, {
            "status": "dry_run_would_import",
            "successful_imports": 0,
            "would_import": len(ledger),
            "target_successful_imports": target_successful_imports,
            "acceptable_range": [min_successful_imports, max_successful_imports],
            "target_met": False,
        }, []

    from app.utils.media_helpers import get_unique_filename
    from app.utils.media_processor import process_media_file
    from app.utils.thumbnail_generator import generate_thumbnail

    ledger: list[dict[str, Any]] = []
    imported_ids: list[int] = []
    failure_reasons: Counter[str] = Counter()
    consecutive_failures = 0
    attempted = 0
    for row in candidates:
        if len(imported_ids) >= target_successful_imports:
            break
        attempted += 1
        source = Path(str(row["source_locator_private_ref"]))
        tmp_path: Path | None = None
        final_path: Path | None = None
        thumb_path: Path | None = None
        media_id: int | None = None
        status = "failed"
        failure_reason: str | None = None
        bytes_copied = 0
        try:
            unique_filename = get_unique_filename(context.original_dir, source.name)
            final_path = context.original_dir / unique_filename
            tmp_path = context.original_dir / f".e1-{uuid.uuid4().hex}{source.suffix.lower()}.tmp"
            copy_result = copy_with_timeout(source, tmp_path, copy_timeout_seconds)
            if not copy_result.get("ok"):
                raise E1BlockedError(str(copy_result.get("error_reason") or "copy_failed"))
            bytes_copied = int(copy_result.get("bytes_copied") or 0)
            tmp_path.replace(final_path)
            metadata = process_media_file(final_path)
            if metadata.get("hash") != row.get("file_hash"):
                raise E1BlockedError("copied_hash_mismatch")
            thumb_name = get_unique_filename(context.thumbnail_dir, f"{final_path.stem}.jpg")
            thumb_path = context.thumbnail_dir / thumb_name
            if not generate_thumbnail(final_path, thumb_path, metadata["file_type"]):
                raise E1BlockedError("thumbnail_generation_failed")
            managed_path = storage_relative(final_path, context.storage_root)
            managed_thumb = storage_relative(thumb_path, context.storage_root)
            with engine.begin() as conn:
                existing = conn.execute(
                    text("SELECT id FROM blombooru_media WHERE hash = :hash LIMIT 1"),
                    {"hash": row.get("file_hash")},
                ).scalar()
                if existing:
                    status = "duplicate_by_hash_race"
                    media_id = None
                    failure_reason = "duplicate_by_hash_race"
                    try:
                        final_path.unlink(missing_ok=True)
                        thumb_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                else:
                    media_id = insert_imported_media(
                        conn,
                        filename=final_path.name,
                        managed_path=managed_path,
                        managed_thumbnail=managed_thumb,
                        metadata=metadata,
                    )
                    status = "imported"
                    imported_ids.append(media_id)
                    consecutive_failures = 0
        except Exception as exc:
            failure_reason = str(exc).split(":", 1)[0][:120] or "import_failed"
            failure_reasons[failure_reason] += 1
            consecutive_failures += 1
            for created in (tmp_path, final_path, thumb_path):
                try:
                    if created and created.exists():
                        created.unlink()
                except OSError:
                    pass
        ledger.append(
            {
                "run_id": context.run_id,
                "candidate_id": row["candidate_id"],
                "public_safe_label": row["public_safe_label"],
                "status": status,
                "media_id": media_id,
                "duplicate_of_media_id": None,
                "failure_reason": failure_reason,
                "bytes_copied": bytes_copied if status == "imported" else 0,
                "eligible_for_db_import": row.get("eligible_for_import", False),
            }
        )
        budget = evaluate_failure_budget(
            attempted=attempted,
            failure_reasons=failure_reasons,
            consecutive_failures=consecutive_failures,
            max_item_failures=int(failure_budget["max_item_failures"]),
            max_failure_rate=float(failure_budget["max_failure_rate"]),
            max_same_reason_failures=int(failure_budget["max_same_reason_failures"]),
            max_consecutive_failures=int(failure_budget["max_consecutive_failures"]),
        )
        if not budget["passed"]:
            break
    status = "completed" if min_successful_imports <= len(imported_ids) <= max_successful_imports else "completed_target_not_met"
    if len(imported_ids) == target_successful_imports:
        status = "completed_recommended_target_met"
    return ledger, {
        "status": status,
        "successful_imports": len(imported_ids),
        "target_successful_imports": target_successful_imports,
        "acceptable_range": [min_successful_imports, max_successful_imports],
        "target_met": min_successful_imports <= len(imported_ids) <= max_successful_imports,
        "recommended_target_met": len(imported_ids) == target_successful_imports,
        "attempted_imports": attempted,
        "failure_count": sum(failure_reasons.values()),
        "failure_reason_counts": dict(sorted(failure_reasons.items())),
        "app_managed_storage_writes": len(imported_ids),
        "source_root_mutation": False,
        "db_import": True,
    }, imported_ids


def init_app_database_session() -> Any:
    from app import database as app_database

    if app_database.SessionLocal is None:
        app_database.init_engine()
    if app_database.SessionLocal is None:
        raise E1BlockedError("app_database_session_unavailable")
    return app_database.SessionLocal


def run_classification(imported_media_ids: Sequence[int], chunk_size: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not imported_media_ids:
        return [], {"status": "noop_no_imported_media", "processed": 0, "failed": 0, "distribution": {}}
    from app.config import settings
    from app.models import ClassificationJob, Media
    from app.services.classification_job_service import create_classification_job, run_classification_job

    SessionLocal = init_app_database_session()
    effective_chunk = min(max(1, chunk_size), int(settings.CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS))
    job_records: list[dict[str, Any]] = []
    for start in range(0, len(imported_media_ids), effective_chunk):
        chunk = list(imported_media_ids[start : start + effective_chunk])
        db = SessionLocal()
        try:
            job = create_classification_job(
                db,
                media_ids=chunk,
                max_items=len(chunk),
                only_unclassified=False,
                force_reclassify=False,
                trigger_source=TRIGGER_SOURCE,
            )
            job_id = int(job.id)
        finally:
            db.close()
        run_classification_job(job_id)
        db = SessionLocal()
        try:
            refreshed = db.get(ClassificationJob, job_id)
            job_records.append(
                {
                    "job_id": job_id,
                    "status": refreshed.status if refreshed else "missing",
                    "processed": int(getattr(refreshed, "processed", 0) or 0),
                    "failed": int(getattr(refreshed, "failed", 0) or 0),
                    "classified_anime": int(getattr(refreshed, "classified_anime", 0) or 0),
                    "classified_unknown": int(getattr(refreshed, "classified_unknown", 0) or 0),
                    "classified_non_anime": int(getattr(refreshed, "classified_non_anime", 0) or 0),
                    "error_message": str(getattr(refreshed, "error_message", "") or "")[:500],
                }
            )
        finally:
            db.close()
        if job_records[-1]["status"] != "completed":
            break
    db = SessionLocal()
    try:
        rows = db.query(Media.id, Media.content_class).filter(Media.id.in_(list(imported_media_ids))).all()
        class_by_id = {int(row.id): (row.content_class.value if hasattr(row.content_class, "value") else str(row.content_class or "")) for row in rows}
    finally:
        db.close()
    distribution = Counter(class_by_id.values())
    failed_or_unclassified = sum(1 for media_id in imported_media_ids if not class_by_id.get(int(media_id)))
    if failed_or_unclassified:
        distribution["failed_or_unclassified"] += failed_or_unclassified
    ledger = [
        {
            "run_id": "",
            "media_id": int(media_id),
            "content_class": class_by_id.get(int(media_id)) or None,
            "eligible_for_ai_tagging": class_by_id.get(int(media_id)) in {"anime", "unknown"},
            "classification_success": bool(class_by_id.get(int(media_id))),
        }
        for media_id in imported_media_ids
    ]
    status = "completed" if all(job["status"] == "completed" for job in job_records) and not failed_or_unclassified else "failed"
    return ledger, {
        "status": status,
        "jobs": job_records,
        "processed": sum(job["processed"] for job in job_records),
        "failed": sum(job["failed"] for job in job_records) + failed_or_unclassified,
        "distribution": dict(sorted(distribution.items())),
        "anime": distribution.get("anime", 0),
        "unknown": distribution.get("unknown", 0),
        "non_anime": distribution.get("non_anime", 0),
        "ineligible": distribution.get("non_anime", 0) + distribution.get("illustration", 0),
        "eligible": distribution.get("anime", 0) + distribution.get("unknown", 0),
    }


def ai_tag_counts_for_media(media_ids: Sequence[int]) -> dict[int, int]:
    if not media_ids:
        return {}
    from app.models import blombooru_media_tags

    SessionLocal = init_app_database_session()
    db = SessionLocal()
    try:
        rows = (
            db.query(blombooru_media_tags.c.media_id, text("COUNT(*) AS tag_count"))
            .filter(blombooru_media_tags.c.media_id.in_(list(media_ids)))
            .filter(blombooru_media_tags.c.source == "ai_wd")
            .group_by(blombooru_media_tags.c.media_id)
            .all()
        )
        return {int(row[0]): int(row[1]) for row in rows}
    finally:
        db.close()


def run_ai_tagging(
    eligible_media_ids: Sequence[int],
    chunk_size: int,
    *,
    failure_budget: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    os.environ["CONTENT_CLASSIFICATION_ENABLED"] = "false"
    os.environ["AI_TAGGING_AUTO_LOCALIZATION"] = "false"
    os.environ["TAG_TRANSLATION_AUTO_ENABLED"] = "false"
    os.environ["TAG_TRANSLATION_BACKGROUND_ENABLED"] = "false"
    os.environ["TAG_TRANSLATION_LLM_ENABLED"] = "false"
    if not eligible_media_ids:
        return [], [], {
            "status": "noop_no_eligible_media",
            "eligible_new_media": 0,
            "ai_tag_success_count": 0,
            "ai_tag_failure_count": 0,
            "coverage_ratio": 1.0,
            "coverage_pct": 100.0,
        }

    from app.config import settings
    from app.models import AITagJob
    from app.services.ai_tagging_job_service import create_ai_tag_job, run_ai_tag_job
    from app.services.ai_tagging_service import check_model_status

    model_status = check_model_status()
    if model_status.get("model_downloaded") is False:
        raise E1BlockedError("ai_model_not_available_offline")

    SessionLocal = init_app_database_session()
    effective_chunk = min(max(1, chunk_size), int(settings.AI_TAGGING_BATCH_MAX_ITEMS))
    media_to_job: dict[int, int] = {}
    job_records: list[dict[str, Any]] = []
    for start in range(0, len(eligible_media_ids), effective_chunk):
        chunk = list(eligible_media_ids[start : start + effective_chunk])
        db = SessionLocal()
        try:
            job = create_ai_tag_job(
                db,
                media_ids=chunk,
                max_items=len(chunk),
                dry_run=False,
                only_without_ai_tags=True,
                force_suggestions=False,
                trigger_source=TRIGGER_SOURCE,
            )
            job_id = int(job.id)
            for media_id in chunk:
                media_to_job[int(media_id)] = job_id
        finally:
            db.close()
        run_ai_tag_job(job_id)
        db = SessionLocal()
        try:
            refreshed = db.get(AITagJob, job_id)
            job_records.append(
                {
                    "job_id": job_id,
                    "status": refreshed.status if refreshed else "missing",
                    "processed": int(getattr(refreshed, "processed", 0) or 0),
                    "failed": int(getattr(refreshed, "failed", 0) or 0),
                    "tags_added": int(getattr(refreshed, "tags_added", 0) or 0),
                    "suggestions_added": int(getattr(refreshed, "suggestions_added", 0) or 0),
                    "localization_status": str(getattr(refreshed, "localization_status", "") or ""),
                    "error_message": str(getattr(refreshed, "error_message", "") or "")[:500],
                }
            )
        finally:
            db.close()
        if job_records[-1]["status"] != "completed":
            break

    tag_counts = ai_tag_counts_for_media(eligible_media_ids)
    ledger: list[dict[str, Any]] = []
    failure_rows: list[dict[str, Any]] = []
    failure_reasons: Counter[str] = Counter()
    for media_id in eligible_media_ids:
        count = int(tag_counts.get(int(media_id), 0))
        success = count > 0
        reason = None if success else "missing_ai_tag_provenance"
        if reason:
            failure_reasons[reason] += 1
        row = {
            "run_id": "",
            "media_id": int(media_id),
            "ai_tag_attempted": True,
            "ai_tag_success": success,
            "failure_reason": reason,
            "job_id": media_to_job.get(int(media_id)),
            "output_tag_count": count,
            "has_ai_tag_provenance": success,
        }
        ledger.append(row)
        if not success:
            failure_rows.append(row)
    budget = evaluate_failure_budget(
        attempted=len(eligible_media_ids),
        failure_reasons=failure_reasons,
        consecutive_failures=0,
        max_item_failures=int(failure_budget["max_item_failures"]),
        max_failure_rate=float(failure_budget["max_failure_rate"]),
        max_same_reason_failures=int(failure_budget["max_same_reason_failures"]),
        max_consecutive_failures=int(failure_budget["max_consecutive_failures"]),
    )
    continuity = ai_tag_continuity_result(len(eligible_media_ids), len(eligible_media_ids) - len(failure_rows), len(failure_rows))
    status = "completed" if all(job["status"] == "completed" for job in job_records) and continuity["coverage_ratio"] == 1.0 else "failed"
    return ledger, failure_rows, {
    "status": status,
        "jobs": job_records,
        "eligible_new_media": len(eligible_media_ids),
        "ai_tag_success_count": len(eligible_media_ids) - len(failure_rows),
        "ai_tag_failure_count": len(failure_rows),
        "coverage_ratio": continuity["coverage_ratio"],
        "coverage_pct": continuity["coverage_pct"],
        "continuity_policy": continuity,
        "failure_budget": budget,
        "model_status": {
            "enabled": model_status.get("enabled"),
            "model_name": model_status.get("model_name"),
            "available": model_status.get("available"),
            "model_downloaded": model_status.get("model_downloaded"),
            "network_download_allowed": False,
        },
        "localization_auto_disabled": True,
    }


def preflight_local_model_availability() -> dict[str, Any]:
    from app.config import settings
    from app.services.ai_tagging_service import check_model_status

    ai_status = check_model_status()
    if not ai_status.get("available") or ai_status.get("model_downloaded") is not True:
        raise E1BlockedError("ai_model_not_available_offline")

    clip_status: dict[str, Any] = {"required": settings.CONTENT_CLASSIFICATION_METHOD == "clip"}
    if clip_status["required"]:
        from app.services.clip_classifier import CLIPClassifier

        clip = CLIPClassifier()
        loaded = clip.ensure_loaded()
        clip_status.update(
            {
                "loaded": bool(loaded),
                "error": getattr(clip, "_load_error", None),
                "network_download_allowed": False,
            }
        )
        if not loaded:
            raise E1BlockedError("clip_model_not_available_offline")

    return {
        "ai_model": {
            "enabled": ai_status.get("enabled"),
            "model_name": ai_status.get("model_name"),
            "available": ai_status.get("available"),
            "model_downloaded": ai_status.get("model_downloaded"),
            "network_download_allowed": False,
        },
        "classification_model": clip_status,
    }


def eligible_media_ids_from_classification(classification_ledger: Sequence[Mapping[str, Any]]) -> list[int]:
    return [
        int(row["media_id"])
        for row in classification_ledger
        if row.get("content_class") in {"anime", "unknown"} and row.get("classification_success")
    ]


def active_job_counts(conn: Connection) -> dict[str, int]:
    statuses = ("pending", "running", "cancelling")
    result = {}
    for key, table in [
        ("ai_jobs", "blombooru_ai_tag_jobs"),
        ("classification_jobs", "blombooru_classification_jobs"),
        ("translation_jobs", "blombooru_tag_translation_jobs"),
    ]:
        if not table_exists(conn, table):
            result[key] = 0
            continue
        stmt = text(f"SELECT COUNT(*) FROM {qident(table)} WHERE status IN :statuses").bindparams(
            bindparam("statuses", expanding=True)
        )
        result[key] = int(conn.execute(stmt, {"statuses": list(statuses)}).scalar() or 0)
    return result


def public_report_markdown(summary: Mapping[str, Any]) -> str:
    baseline = summary["baseline_before"]
    candidate = summary["candidate_discovery"]
    duplicates = summary["duplicate_detection"]
    imports = summary["import_results"]
    cls = summary["classification_results"]
    ai = summary["ai_tagging_results"]
    mutation = summary["mutation_proof"]
    lines = [
        f"# {PHASE} {PHASE_TITLE}",
        "",
        "## Summary",
        f"- Status: `{summary.get('status')}`.",
        f"- Mode: `{summary.get('mode')}`.",
        f"- Successful imports: `{imports.get('successful_imports')}`; acceptable range `{imports.get('acceptable_range')}`.",
        f"- AI tag coverage for newly imported eligible media: `{ai.get('ai_tag_success_count')}` / `{ai.get('eligible_new_media')}` (`{ai.get('coverage_pct')}`%).",
        f"- Recommended next phase: `{summary.get('recommended_next_phase')}`.",
        "",
        "## Scope and non-goals",
        "- E1 imports a controlled medium batch and completes AI tag provenance for newly imported eligible media.",
        "- Pixiv/source metadata, gallery-dl, provider calls, SourceConcept resolver, Entity bridge, localization, LLM, 5k/10k, and full-library work were not run.",
        "",
        "## Baseline before E1",
        f"- Total media: `{baseline.get('total_media')}`.",
        f"- Eligible media: `{baseline.get('eligible_media')}`.",
        f"- Eligible AI tag provenance: `{baseline.get('eligible_media_with_ai_tag_provenance')}` / `{baseline.get('eligible_media')}` (`{baseline.get('eligible_ai_tag_provenance_pct')}`%).",
        f"- Source metadata coverage by distinct media: `{baseline.get('media_with_source_metadata')}`.",
        "",
        "## Source root and storage safety preflight",
        f"- Safe source roots: `{summary['source_root_safety'].get('safe_source_roots')}`.",
        f"- Source/storage overlap safe: `{summary['source_root_safety'].get('source_storage_overlap_safe')}`.",
        f"- Source roots read-only from E1 perspective: `{summary['source_root_safety'].get('read_only_from_e1_perspective')}`.",
        "",
        "## Candidate discovery",
        f"- Candidates considered: `{candidate.get('total_candidates_considered')}`.",
        f"- Eligible before duplicate/hash checks: `{candidate.get('eligible_pre_duplicate')}`.",
        f"- Candidate target: `{candidate.get('candidate_target')}`.",
        f"- Deferred buckets: `{json.dumps(candidate.get('deferred_reason_counts'), ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Duplicate filtering",
        f"- Selected for duplicate detection: `{duplicates.get('selected_for_duplicate_detection')}`.",
        f"- Unique import candidates: `{duplicates.get('unique_import_candidates')}`.",
        f"- Duplicate count: `{duplicates.get('duplicate_count')}`.",
        f"- Buckets: `{json.dumps(duplicates.get('reason_counts'), ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Import execution",
        f"- Status: `{imports.get('status')}`.",
        f"- Successful imports: `{imports.get('successful_imports')}`.",
        f"- Total media after import: `{summary['db_identity_after'].get('total_media_after')}`.",
        "",
        "## Classification / eligibility results",
        f"- Anime: `{cls.get('anime')}`.",
        f"- Unknown: `{cls.get('unknown')}`.",
        f"- Non-anime: `{cls.get('non_anime')}`.",
        f"- Failed/deferred: `{cls.get('failed')}`.",
        "",
        "## AI tag completion results",
        f"- Eligible new media: `{ai.get('eligible_new_media')}`.",
        f"- AI tag success: `{ai.get('ai_tag_success_count')}`.",
        f"- AI tag failures: `{ai.get('ai_tag_failure_count')}`.",
        f"- Coverage ratio: `{ai.get('coverage_ratio')}`.",
        "",
        "## Mutation proof",
        f"- Expected changed tables: `{json.dumps(mutation.get('expected_changed_table_names'), ensure_ascii=False)}`.",
        f"- Forbidden changed tables: `{json.dumps(mutation.get('forbidden_changed_table_names'), ensure_ascii=False)}`.",
        f"- Unexpected changed tables: `{json.dumps(mutation.get('unexpected_changed_table_names'), ensure_ascii=False)}`.",
        f"- Passed: `{mutation.get('passed')}`.",
        "",
        "## Public/private artifact boundary",
        "- Public artifacts contain aggregate counts and safe labels only.",
        "- Private per-item ledgers remain under the local `.local_manifests` phase directory and are not committed.",
        "",
        "## Failure budget and stop conditions",
        f"- Failure budget: `{json.dumps(summary.get('failure_budget'), ensure_ascii=False, sort_keys=True)}`.",
        f"- Stop conditions: `{json.dumps(summary.get('safety', {}).get('stop_conditions'), ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Decision matrix",
        f"- E1 target met: `{summary['decision_matrix'].get('e1_target_met')}`.",
        f"- PX1 may start next: `{summary['decision_matrix'].get('px1_may_start_next')}`.",
        f"- 5k/10k/full-library remains deferred: `{summary['decision_matrix'].get('broad_import_deferred')}`.",
        "",
        "## Deferred work",
        "- PX1: Pixiv/source metadata extraction remains separate.",
        "- SCV2-R1: SourceConcept resolver/needs_review triage remains separate.",
        "- SCV2-A1: later aggregate audit remains separate.",
        "",
        "## Validation",
        f"- Commands recorded: `{json.dumps(summary.get('validation', {}).get('commands'), ensure_ascii=False)}`.",
        f"- Browser validation: `not run; E1 has no UI/runtime behavior target`.",
        "",
        "## Safety confirmation",
        "- No push main, no merge, no source/iCloud mutation, no cleanup/delete/reset/drop/truncate, no DB import beyond approved E1 media import, no Pixiv/provider/gallery-dl/source metadata, no LLM, no localization, no Entity Resolver, no SourceConcept resolver, no Entity bridge, and no browser/server validation.",
        "",
        "## Artifact lifecycle",
        f"- Runner: `{summary['artifact_lifecycle'].get('runner')}`.",
        f"- Private artifacts: `{summary['artifact_lifecycle'].get('private_artifacts')}`.",
        f"- Public report: `{summary['artifact_lifecycle'].get('public_report')}`.",
        "",
        "## Engineering judgment / operator notes",
        "- E1 is intentionally execution-scoped and narrower than PX1/R1/A1. The safe boundary is app-managed import plus existing local classification/AI jobs only.",
        "- AI tags remain provenance/signal and are not entity truth or confirmed assignments.",
        "- Any remaining item failures are acceptable only when item-level ledgers record the reason and the approved failure budget is not exceeded.",
    ]
    return "\n".join(lines) + "\n"


def public_summary(
    *,
    context: RuntimeContext,
    branch: str,
    baseline_before: Mapping[str, Any],
    db_identity_before: Mapping[str, Any],
    db_identity_after: Mapping[str, Any],
    storage_identity: Mapping[str, Any],
    source_inventory: Sequence[Mapping[str, Any]],
    candidate_summary: Mapping[str, Any],
    duplicate_summary: Mapping[str, Any],
    import_results: Mapping[str, Any],
    classification_results: Mapping[str, Any],
    ai_results: Mapping[str, Any],
    mutation_delta: Mapping[str, Any],
    failure_budget: Mapping[str, Any],
    validation: Mapping[str, Any],
    status: str,
) -> dict[str, Any]:
    safe_source_roots = [row for row in source_inventory if row.get("exists") and row.get("is_dir") and not row.get("under_app_storage")]
    e1_target_met = bool(import_results.get("target_met")) and ai_results.get("coverage_ratio") == 1.0 and mutation_delta.get("passed")
    summary = {
        "phase": PHASE,
        "title": PHASE_TITLE,
        "branch": branch,
        "generated_at": utc_now(),
        "status": status,
        "mode": context.mode,
        "baseline_before": dict(baseline_before),
        "db_identity_before": {
            **{k: v for k, v in db_identity_before.items() if k != "python_executable"},
            "python_executable": Path(str(db_identity_before.get("python_executable") or "")).name,
            "total_media_before": baseline_before.get("total_media"),
        },
        "db_identity_after": {
            **{k: v for k, v in db_identity_after.items() if k != "python_executable"},
            "python_executable": Path(str(db_identity_after.get("python_executable") or "")).name,
        },
        "storage_identity": {
            "storage_root_label": "app_storage",
            "original_path_label": "app_storage/media/original",
            "thumbnails_path_label": "app_storage/media/thumbnails",
            "storage_root_exists": storage_identity.get("storage_root_exists"),
            "original_path_exists": storage_identity.get("original_path_exists"),
            "thumbnails_path_exists": storage_identity.get("thumbnails_path_exists"),
            "source_storage_overlap_safe": storage_identity.get("source_storage_overlap_safe"),
            "paths_redacted": True,
        },
        "source_root_safety": {
            "safe_source_roots": len(safe_source_roots),
            "source_roots_total": len(source_inventory),
            "source_storage_overlap_safe": storage_identity.get("source_storage_overlap_safe"),
            "read_only_from_e1_perspective": all(row.get("read_only_from_e1_perspective") for row in source_inventory),
            "icloud_or_cloud_backed_source_roots": sum(1 for row in source_inventory if row.get("icloud_or_cloud_backed_hint")),
            "source_paths_public": False,
        },
        "candidate_discovery": dict(candidate_summary),
        "duplicate_detection": dict(duplicate_summary),
        "import_results": dict(import_results),
        "classification_results": dict(classification_results),
        "ai_tagging_results": dict(ai_results),
        "mutation_proof": {
            "passed": mutation_delta.get("passed"),
            "expected_changed_tables": mutation_delta.get("expected_changed_tables"),
            "forbidden_changed_tables": mutation_delta.get("forbidden_changed_tables"),
            "unexpected_changed_tables": mutation_delta.get("unexpected_changed_tables"),
            "expected_changed_table_names": [row["table"] for row in mutation_delta.get("expected_changed_tables", [])],
            "forbidden_changed_table_names": [row["table"] for row in mutation_delta.get("forbidden_changed_tables", [])],
            "unexpected_changed_table_names": [row["table"] for row in mutation_delta.get("unexpected_changed_tables", [])],
            "missing_forbidden_tables": mutation_delta.get("missing_forbidden_tables"),
        },
        "failure_budget": dict(failure_budget),
        "public_redaction": {"passed": False, "findings": [], "checked_paths": []},
        "decision_matrix": {
            "e1_target_met": e1_target_met,
            "px1_may_start_next": e1_target_met,
            "broad_import_deferred": True,
            "pixiv_source_metadata_not_run_reason": "PX1 owns provider/gallery-dl/Pixiv/source metadata extraction.",
            "source_concept_resolver_not_run_reason": "SCV2-R1 owns resolver/needs_review triage.",
            "five_k_ten_k_full_library_deferred_reason": "Requires stronger production ingestion/source-item ledger and broad-run discipline.",
        },
        "recommended_next_phase": "PX1" if e1_target_met else "Resolve E1 blockers before PX1",
        "validation": dict(validation),
        "safety": {
            "phase_boundary": phase_boundary_status(),
            "no_push_main": True,
            "no_merge": True,
            "no_source_icloud_mutation": True,
            "no_cleanup_delete_reset_drop_truncate": True,
            "no_unapproved_db_import": True,
            "no_classification_ai_beyond_new_imported_eligible": True,
            "no_localization_llm_provider_pixiv_gallery_dl": True,
            "no_entity_resolver_similarity_sourceconcept_entity_bridge": True,
            "browser_validation": "not_run_no_ui_runtime_target",
            "stop_conditions": {
                "forbidden_table_changed": bool(mutation_delta.get("forbidden_changed_tables")),
                "unexpected_table_changed": bool(mutation_delta.get("unexpected_changed_tables")),
                "target_not_met": not bool(import_results.get("target_met")) if context.mode == "execute" else False,
                "ai_continuity_not_met": ai_results.get("coverage_ratio") not in {None, 1.0} if context.mode == "execute" else False,
            },
        },
        "artifact_lifecycle": {
            "runner": "phase-scoped operational runner",
            "tests": "phase-scoped validation test",
            "private_artifacts": "one-off local artifact / ignored output",
            "public_report": "public report / handoff / roadmap update",
        },
        "private_artifacts": {
            "private_artifact_root_label": f".local_manifests/{PHASE_SLUG}",
            "private_artifact_names": PRIVATE_ARTIFACT_NAMES,
            "private_artifacts_committed": False,
            "paths_public": False,
        },
    }
    schema = validate_summary_schema(summary)
    if not schema["passed"]:
        raise E1BlockedError("summary_schema_missing:" + ",".join(schema["missing_fields"]))
    return summary


def write_public_outputs(summary: dict[str, Any]) -> dict[str, Any]:
    temp_summary = dict(summary)
    temp_summary["public_redaction"] = {"passed": True, "findings": [], "checked_paths": []}
    write_text(PUBLIC_REPORT_MD, public_report_markdown(temp_summary))
    write_json(PUBLIC_REPORT_JSON, temp_summary)
    redaction = scan_public_artifacts([PUBLIC_REPORT_MD, PUBLIC_REPORT_JSON])
    summary["public_redaction"] = redaction
    if not redaction["passed"]:
        write_json(PUBLIC_REPORT_JSON, summary)
        write_text(
            PUBLIC_REPORT_MD,
            f"# {PHASE} {PHASE_TITLE}\n\nPublic report blocked by redaction scan. Private diagnostics remain local.\n",
        )
        raise E1BlockedError("public_redaction_failed")
    write_text(PUBLIC_REPORT_MD, public_report_markdown(summary))
    write_json(PUBLIC_REPORT_JSON, summary)
    return redaction


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    apply_phase_env_overrides()
    engine, db_source_identity = create_engine_for_phase()
    context = load_runtime_context(args, db_source_identity)
    context.output_dir.mkdir(parents=True, exist_ok=True)
    failure_budget = {
        "scope": "import execution and AI tagging; candidate discovery deferrals are separately item-ledgered and do not count against import/AI failure budget",
        "max_item_failures": args.max_item_failures,
        "max_failure_rate": args.max_failure_rate,
        "max_same_reason_failures": args.max_same_reason_failures,
        "max_consecutive_failures": args.max_consecutive_failures,
    }
    command_for_report = " ".join(
        [Path(sys.executable).name, *[arg.replace("\\", "/") for arg in sys.argv]]
    )
    validation = {
        "commands": [command_for_report],
        "dry_run_first_required": True,
        "browser_validation": "not_run_no_ui_runtime_target",
        "server_started": False,
    }
    source_roots = unique_paths([*env_local_library_paths(), *source_roots_from_local_manifests()])
    storage_identity = build_storage_identity(source_roots, context.storage_root)
    source_inventory = build_source_root_inventory(source_roots, context.storage_root)
    write_json(context.output_dir / "storage-identity.json", storage_identity)
    write_json(context.output_dir / "source-root-inventory.json", source_inventory)
    assert_storage_preflight(storage_identity, source_inventory)
    safe_source_roots = [
        Path(row["source_root_private"])
        for row in source_inventory
        if row.get("exists") and row.get("is_dir") and not row.get("under_app_storage")
    ]
    branch = git_value(["branch", "--show-current"])
    if branch != "codex/phase45-scv2-e1-medium-import-ai-tag-completion":
        raise E1BlockedError(f"wrong_branch:{branch}")

    with engine.connect() as conn:
        db_identity_before = public_db_identity(db_source_identity, conn)
        assert_db_preflight(db_source_identity, db_identity_before)
        active_jobs = active_job_counts(conn)
        if any(active_jobs.values()):
            raise E1BlockedError("active_background_job_rows:" + json.dumps(active_jobs, sort_keys=True))
        baseline_before = build_baseline(conn)
        mutation_before = build_table_counts(conn)
    write_json(context.output_dir / "db-identity-before.json", db_identity_before)
    write_json(context.output_dir / "mutation-proof-before.json", mutation_before)

    candidates, candidate_summary = discover_candidates(
        run_id=context.run_id,
        source_roots=safe_source_roots,
        storage_root=context.storage_root,
        max_discovery_files=args.max_discovery_files,
        max_file_size_mb=args.max_file_size_mb,
    )
    candidate_summary["candidate_target"] = args.candidate_target
    write_jsonl(context.output_dir / "candidate-discovery-ledger.jsonl", candidates)
    write_json(context.output_dir / "candidate-summary.json", candidate_summary)
    if candidate_summary["eligible_pre_duplicate"] < args.min_successful_imports and args.execute:
        raise E1BlockedError(f"not_enough_pre_duplicate_candidates:{candidate_summary['eligible_pre_duplicate']}")

    with engine.connect() as conn:
        duplicate_rows, duplicate_summary = run_duplicate_detection(
            conn,
            candidates,
            candidate_target=args.candidate_target,
            hash_timeout_seconds=args.hash_timeout_seconds,
        )
    write_jsonl(context.output_dir / "duplicate-detection-ledger.jsonl", duplicate_rows)
    if duplicate_summary["unique_import_candidates"] < args.min_successful_imports and args.execute:
        raise E1BlockedError(f"not_enough_unique_candidates:{duplicate_summary['unique_import_candidates']}")

    if args.execute:
        validation["model_preflight"] = preflight_local_model_availability()

    import_ledger, import_results, imported_media_ids = execute_imports(
        engine,
        context,
        duplicate_rows,
        execute=args.execute,
        target_successful_imports=args.target_successful_imports,
        min_successful_imports=args.min_successful_imports,
        max_successful_imports=args.max_successful_imports,
        copy_timeout_seconds=args.copy_timeout_seconds,
        failure_budget=failure_budget,
    )
    write_jsonl(context.output_dir / "import-item-ledger.jsonl", import_ledger)

    classification_ledger: list[dict[str, Any]] = []
    classification_results: dict[str, Any] = {
        "status": "dry_run_not_executed",
        "processed": 0,
        "failed": 0,
        "distribution": {},
        "anime": 0,
        "unknown": 0,
        "non_anime": 0,
        "eligible": 0,
        "ineligible": 0,
    }
    ai_ledger: list[dict[str, Any]] = []
    ai_failure_ledger: list[dict[str, Any]] = []
    ai_results: dict[str, Any] = {
        "status": "dry_run_not_executed",
        "eligible_new_media": 0,
        "ai_tag_success_count": 0,
        "ai_tag_failure_count": 0,
        "coverage_ratio": None,
        "coverage_pct": None,
    }
    if args.execute:
        classification_ledger, classification_results = run_classification(imported_media_ids, args.classification_chunk_size)
        for row in classification_ledger:
            row["run_id"] = context.run_id
        write_jsonl(context.output_dir / "classification-ledger.jsonl", classification_ledger)
        if classification_results.get("status") != "completed":
            raise E1BlockedError("classification_failed")
        eligible_ids = eligible_media_ids_from_classification(classification_ledger)
        ai_ledger, ai_failure_ledger, ai_results = run_ai_tagging(
            eligible_ids,
            args.ai_chunk_size,
            failure_budget=failure_budget,
        )
        for row in ai_ledger:
            row["run_id"] = context.run_id
        for row in ai_failure_ledger:
            row["run_id"] = context.run_id
        write_jsonl(context.output_dir / "ai-tagging-ledger.jsonl", ai_ledger)
        write_jsonl(context.output_dir / "ai-tagging-failure-ledger.jsonl", ai_failure_ledger)
        if ai_results.get("status") != "completed":
            raise E1BlockedError("ai_tagging_failed")
    else:
        write_jsonl(context.output_dir / "classification-ledger.jsonl", [])
        write_jsonl(context.output_dir / "ai-tagging-ledger.jsonl", [])
        write_jsonl(context.output_dir / "ai-tagging-failure-ledger.jsonl", [])

    with engine.connect() as conn:
        db_identity_after = public_db_identity(db_source_identity, conn)
        baseline_after = build_baseline(conn)
        db_identity_after["total_media_after"] = baseline_after.get("total_media")
        mutation_after = build_table_counts(conn)
    write_json(context.output_dir / "db-identity-after.json", db_identity_after)
    write_json(context.output_dir / "mutation-proof-after.json", mutation_after)
    mutation_delta = classify_table_mutations(mutation_before, mutation_after)
    write_json(context.output_dir / "mutation-proof-delta.json", mutation_delta)
    write_json(context.output_dir / "safety-stop-conditions.json", {"failure_budget": failure_budget, "phase_boundary": phase_boundary_status()})
    if args.execute and not mutation_delta["passed"]:
        raise E1BlockedError("forbidden_or_unexpected_table_mutation")

    status = "completed" if (not args.execute or (import_results.get("target_met") and ai_results.get("coverage_ratio") == 1.0 and mutation_delta["passed"])) else "completed_with_blockers"
    summary = public_summary(
        context=context,
        branch=branch,
        baseline_before=baseline_before,
        db_identity_before=db_identity_before,
        db_identity_after=db_identity_after,
        storage_identity=storage_identity,
        source_inventory=source_inventory,
        candidate_summary=candidate_summary,
        duplicate_summary=duplicate_summary,
        import_results=import_results,
        classification_results=classification_results,
        ai_results=ai_results,
        mutation_delta=mutation_delta,
        failure_budget=failure_budget,
        validation=validation,
        status=status,
    )
    if args.write_public_report:
        redaction = write_public_outputs(summary)
        write_text(context.output_dir / "public-redaction-check.txt", json.dumps(redaction, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    else:
        write_text(context.output_dir / "public-redaction-check.txt", "public report not requested\n")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-execution", default="")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write-public-report", action="store_true")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--target-successful-imports", type=int, default=TARGET_SUCCESSFUL_IMPORTS)
    parser.add_argument("--min-successful-imports", type=int, default=MIN_SUCCESSFUL_IMPORTS)
    parser.add_argument("--max-successful-imports", type=int, default=MAX_SUCCESSFUL_IMPORTS)
    parser.add_argument("--candidate-target", type=int, default=DEFAULT_CANDIDATE_TARGET)
    parser.add_argument("--max-discovery-files", type=int, default=DEFAULT_MAX_DISCOVERY_FILES)
    parser.add_argument("--max-file-size-mb", type=int, default=200)
    parser.add_argument("--hash-timeout-seconds", type=int, default=DEFAULT_HASH_TIMEOUT_SECONDS)
    parser.add_argument("--copy-timeout-seconds", type=int, default=DEFAULT_COPY_TIMEOUT_SECONDS)
    parser.add_argument("--classification-chunk-size", type=int, default=500)
    parser.add_argument("--ai-chunk-size", type=int, default=200)
    parser.add_argument("--max-item-failures", type=int, default=20)
    parser.add_argument("--max-failure-rate", type=float, default=0.05)
    parser.add_argument("--max-same-reason-failures", type=int, default=20)
    parser.add_argument("--max-consecutive-failures", type=int, default=10)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    multiprocessing.freeze_support()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.execute and args.confirm_execution != CONFIRM_PHRASE:
        parser.error(f"--execute requires --confirm-execution {CONFIRM_PHRASE}")
    if args.candidate_target <= 0:
        parser.error("--candidate-target must be positive")
    try:
        summary = run_pipeline(args)
    except E1BlockedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True, default=json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
