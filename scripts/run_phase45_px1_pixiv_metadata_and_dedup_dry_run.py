#!/usr/bin/env python3
"""Phase 4.5-PX1 bounded Pixiv metadata extraction and duplicate dry-run.

Lifecycle: phase-scoped operational runner.

This runner inventories current DB Pixiv-like media, produces an exact-hash
duplicate cleanup dry-run plan, and, when explicitly confirmed, performs a
bounded metadata-only gallery-dl/Pixiv request batch.  Duplicate cleanup is
always read-only in PX1.  Provider metadata writes are limited to the approved
source-layer tables and are guarded by mutation proof.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from sqlalchemy import URL, bindparam, create_engine, text
from sqlalchemy.engine import Connection, Engine

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts import run_phase45_scv2_p0_controlled_medium_expansion_policy as p0  # noqa: E402

PHASE = "4.5-PX1"
PHASE_TITLE = "Bounded Pixiv Metadata Extraction and Exact-Duplicate Cleanup Dry-Run"
PHASE_SLUG = "phase-4.5-px1-pixiv-metadata-dedup-dry-run"
BRANCH = "codex/phase45-px1-pixiv-metadata-dedup-dry-run"
CONFIRM_PHRASE = "EXECUTE_PHASE45_PX1_PIXIV_METADATA_ONLY"

DEFAULT_OUTPUT_DIR = ROOT / ".local_manifests" / PHASE_SLUG
PUBLIC_REPORT_MD = ROOT / "docs" / "reports" / f"{PHASE_SLUG}.md"
PUBLIC_REPORT_JSON = ROOT / "docs" / "reports" / f"{PHASE_SLUG}-summary.json"

PRIVATE_ARTIFACT_NAMES = [
    "db-identity-before.json",
    "db-identity-after.json",
    "pixiv-like-inventory.json",
    "pixiv-like-candidates.jsonl",
    "pixiv-metadata-request-ledger.jsonl",
    "pixiv-metadata-success-ledger.jsonl",
    "pixiv-metadata-failure-ledger.jsonl",
    "source-metadata-write-ledger.jsonl",
    "duplicate-groups-dry-run.jsonl",
    "duplicate-cleanup-plan.jsonl",
    "duplicate-cleanup-summary.json",
    "mutation-proof-before.json",
    "mutation-proof-after.json",
    "mutation-proof-delta.json",
    "provider-cache-summary.json",
    "public-redaction-check.txt",
]

SUMMARY_REQUIRED_FIELDS = {
    "phase",
    "title",
    "branch",
    "generated_at",
    "db_identity_before",
    "db_identity_after",
    "baseline",
    "pixiv_like_inventory",
    "duplicate_dry_run",
    "duplicate_cleanup_plan_summary",
    "metadata_selection",
    "provider_preflight",
    "metadata_extraction_results",
    "source_layer_write_results",
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

ALLOWED_SOURCE_WRITE_TABLES = {
    "blombooru_source_metadata_records",
    "blombooru_source_tag_observations",
    "blombooru_source_name_observations",
    "blombooru_source_metadata_evidence",
    "blombooru_source_searchable_name_assertions",
}

FORBIDDEN_WRITE_TABLES = {
    "blombooru_media",
    "blombooru_media_tags",
    "blombooru_tags",
    "blombooru_tag_translations",
    "blombooru_tag_translation_jobs",
    "blombooru_entities",
    "blombooru_entity_aliases",
    "blombooru_entity_external_identities",
    "blombooru_entity_evidence",
    "blombooru_entity_translations",
    "blombooru_media_entity_candidates",
    "blombooru_media_entity_assignments",
    "blombooru_provider_cache",
    "blombooru_negative_lookup_cache",
    "blombooru_scan_jobs",
    "blombooru_scan_job_media",
    "blombooru_ai_tag_jobs",
    "blombooru_classification_jobs",
    "blombooru_source_tag_registry",
    "blombooru_source_name_registry",
    "blombooru_source_name_alias_candidates",
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
    "blombooru_pixiv_tag_taxonomy_kb",
    "blombooru_pixiv_tag_alias_kb",
}

PIXIV_FILENAME_PATTERNS = (
    re.compile(r"(?<!\d)(?P<work_id>\d{6,12})[_-]p(?P<page_index>\d+)(?!\d)", re.IGNORECASE),
    re.compile(r"(?<!\w)pixiv[_ -]?(?P<work_id>\d{6,12})(?:[_-]p(?P<page_index>\d+))?(?!\d)", re.IGNORECASE),
    re.compile(r"(?<!\w)illust(?:ration)?[_ -]?(?P<work_id>\d{6,12})(?:[_-]p(?P<page_index>\d+))?(?!\d)", re.IGNORECASE),
)
PIXIV_SOURCE_PATTERNS = (
    re.compile(r"pixiv\.net/(?:en/)?artworks/(?P<work_id>\d{6,12})", re.IGNORECASE),
    re.compile(r"(?:illust_id|artwork_id|artworks?)[=:/_-](?P<work_id>\d{6,12})", re.IGNORECASE),
    re.compile(r"(?<!\d)(?P<work_id>\d{6,12})[_-]p(?P<page_index>\d+)(?!\d)", re.IGNORECASE),
)
PIXIV_MARKER_RE = re.compile(r"(?i)(^|[^a-z0-9])(pixiv|pximg|artworks?|illust_id|illust)([^a-z0-9]|$)")

SECRET_RE = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9._~+\-/]{8,}|"
    r"(?:access|refresh)[_-]?token\s*[=:]\s*\S+|"
    r"(?:authorization|cookie|api[_-]?key|password|secret)\s*[=:]\s*\S+|"
    r"sk-[A-Za-z0-9_-]{12,})"
)
LOCAL_PATH_RE = re.compile(
    r"(?i)((?<![A-Za-z])[A-Z]:[\\/]|file://|\\\\|/(?:Users|home|mnt|Volumes|workspace|tmp)(?:/|$)|\\Users\\)"
)
MEDIA_FILENAME_RE = re.compile(
    r"(?i)\b[A-Za-z0-9][A-Za-z0-9_. -]{0,120}\.(jpg|jpeg|png|webp|gif|bmp|avif|mp4|webm|mov)\b"
)
AUTH_ERROR_RE = re.compile(
    r"(?i)("
    r"auth(?:entication|orization)?\s*(?:failed|required|error)|"
    r"login\s*(?:required|failed)|"
    r"not\s+logged\s+in|"
    r"cookie(?:s)?\s*(?:missing|required|invalid|expired)|"
    r"(?:access|refresh)[_-]?token\s*(?:missing|required|invalid|expired|failed)|"
    r"401|403|forbidden|unauthorized"
    r")"
)
RATE_LIMIT_RE = re.compile(r"(?i)(rate.?limit|too many requests|429|retry-after)")
UNAVAILABLE_RE = re.compile(
    r"(?i)(deleted|private|not\s*found|notfound|could\s+not\s+be\s+found|404|"
    r"unavailable|does\s+not\s+exist|removed|suspended)"
)
COMMAND_OPTION_RE = re.compile(r"(?i)(unrecognized|unknown option|invalid option|usage:|no such option)")

PX1_SEARCHABLE_ASSERTION_STATUS = "needs_review"
PX1_SEARCHABLE_ASSERTION_SCHEMA_VERSION = "phase45_px1_direct_source_metadata_v2"


class PX1BlockedError(RuntimeError):
    """Raised for hard PX1 safety/precondition failures."""


@dataclass(frozen=True)
class PixivIdMatch:
    work_id: str
    page_index: int | None
    source_kind: str
    confidence: str = "strong"


@dataclass(frozen=True)
class MediaCandidate:
    media_id: int
    filename: str = ""
    source: str = ""
    content_class: str | None = None
    file_hash: str | None = None
    file_size: int | None = None
    pixiv_like: bool = False
    pixiv_work_ids: tuple[str, ...] = ()
    pixiv_page_indexes: tuple[int | None, ...] = ()
    pixiv_prior_reasons: tuple[str, ...] = ()
    has_any_source_metadata: bool = False
    has_pixiv_source_metadata: bool = False
    ai_tag_count: int = 0
    manual_locked_tag_count: int = 0
    source_metadata_count: int = 0
    source_concept_risk_count: int = 0
    entity_risk_count: int = 0
    album_count: int = 0
    description_present: bool = False

    @property
    def eligible(self) -> bool:
        return str(self.content_class or "").casefold() in {"anime", "unknown"}

    @property
    def provider_execution_eligible(self) -> bool:
        return (
            str(self.content_class or "").casefold() == "anime"
            and self.pixiv_like
            and not self.has_any_source_metadata
            and self.reliable_pixiv_prior
        )

    @property
    def reliable_pixiv_prior(self) -> bool:
        return "filename_pixiv_id_pattern" in self.pixiv_prior_reasons and len(self.pixiv_work_ids) == 1

    @property
    def primary_work_id(self) -> str | None:
        return self.pixiv_work_ids[0] if self.pixiv_work_ids else None

    @property
    def primary_page_index(self) -> int:
        for page in self.pixiv_page_indexes:
            if page is not None:
                return int(page)
        return 0


@dataclass(frozen=True)
class GalleryDlEntrypoint:
    available: bool
    mode: str
    command: tuple[str, ...] = ()
    version: str | None = None
    error: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "mode": self.mode,
            "version_present": self.version is not None,
            "command_label": "project_python -m gallery_dl"
            if self.mode == "project_python_module"
            else ("gallery-dl executable" if self.mode == "external_executable" else self.mode),
            "error": self.error,
        }


@dataclass
class ProviderFailureBudget:
    max_auth_failures: int = 3
    max_rate_limit_failures: int = 3
    max_total_failures: int = 20
    max_failure_rate: float = 0.25
    max_consecutive_failures: int = 5
    auth_failures: int = 0
    rate_limit_failures: int = 0
    total_failures: int = 0
    consecutive_failures: int = 0
    attempts: int = 0
    stopped: bool = False
    stop_reason: str | None = None

    def record_success(self) -> None:
        self.attempts += 1
        self.consecutive_failures = 0

    def record_failure(self, reason: str) -> None:
        self.attempts += 1
        self.total_failures += 1
        self.consecutive_failures += 1
        if reason == "auth_or_config_failure":
            self.auth_failures += 1
        if reason == "rate_limited":
            self.rate_limit_failures += 1
        if self.auth_failures >= self.max_auth_failures:
            self.stopped = True
            self.stop_reason = "repeated_auth_or_config_failures"
        elif self.rate_limit_failures >= self.max_rate_limit_failures:
            self.stopped = True
            self.stop_reason = "repeated_rate_limit_failures"
        elif self.total_failures >= self.max_total_failures:
            self.stopped = True
            self.stop_reason = "max_total_failures"
        elif self.attempts and self.total_failures / self.attempts > self.max_failure_rate and self.total_failures >= 5:
            self.stopped = True
            self.stop_reason = "max_failure_rate"
        elif self.consecutive_failures >= self.max_consecutive_failures:
            self.stopped = True
            self.stop_reason = "max_consecutive_failures"

    def public_dict(self) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "total_failures": self.total_failures,
            "auth_failures": self.auth_failures,
            "rate_limit_failures": self.rate_limit_failures,
            "consecutive_failures": self.consecutive_failures,
            "max_auth_failures": self.max_auth_failures,
            "max_rate_limit_failures": self.max_rate_limit_failures,
            "max_total_failures": self.max_total_failures,
            "max_failure_rate": self.max_failure_rate,
            "max_consecutive_failures": self.max_consecutive_failures,
            "stopped": self.stopped,
            "stop_reason": self.stop_reason,
        }


CompletedRunner = Callable[..., subprocess.CompletedProcess[str]]


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
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8", stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unavailable"


def normalize_text(value: Any) -> str:
    return p0.normalize_source_text(value)


def canonical_key(value: Any) -> str:
    return p0.canonical_source_key(value)


def stable_private_id(value: Any, prefix: str = "item") -> str:
    return p0.stable_private_id(value, prefix)


def percent(part: int, whole: int) -> float:
    return round(part / whole * 100, 2) if whole else 0.0


def qident(value: str) -> str:
    return p0.qident(value)


def table_exists(conn: Connection, table_name: str) -> bool:
    return p0.table_exists(conn, table_name)


def column_exists(conn: Connection, table_name: str, column_name: str) -> bool:
    return p0.column_exists(conn, table_name, column_name)


def count_table(conn: Connection, table_name: str) -> dict[str, Any]:
    return p0.count_table(conn, table_name)


def scalar_count(conn: Connection, sql: str, params: Mapping[str, Any] | None = None) -> int:
    return int(conn.execute(text(sql), params or {}).scalar() or 0)


def rows_dict(conn: Connection, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(text(sql), params or {}).mappings()]


def extract_pixiv_ids_from_text(value: Any, *, source_kind: str = "filename") -> list[PixivIdMatch]:
    text_value = normalize_text(value)
    if not text_value:
        return []
    patterns = PIXIV_SOURCE_PATTERNS if source_kind == "source" else PIXIV_FILENAME_PATTERNS
    found: list[PixivIdMatch] = []
    seen: set[tuple[str, int | None, str]] = set()
    for pattern in patterns:
        for match in pattern.finditer(text_value):
            work_id = str(match.group("work_id"))
            page_raw = match.groupdict().get("page_index")
            page_index = int(page_raw) if page_raw is not None and str(page_raw).isdigit() else None
            key = (work_id, page_index, source_kind)
            if key in seen:
                continue
            seen.add(key)
            found.append(PixivIdMatch(work_id=work_id, page_index=page_index, source_kind=source_kind))
    return found


def extract_pixiv_ids_from_filename(filename: Any) -> list[dict[str, Any]]:
    return [asdict(match) for match in extract_pixiv_ids_from_text(filename, source_kind="filename")]


def classify_pixiv_like_candidate(
    row: Mapping[str, Any],
    *,
    db_signal_categories: Sequence[str] | None = None,
    db_pixiv_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    filename_matches = extract_pixiv_ids_from_text(row.get("filename"), source_kind="filename")
    source_matches = extract_pixiv_ids_from_text(row.get("source"), source_kind="source")
    db_matches = [
        PixivIdMatch(work_id=str(value), page_index=None, source_kind="db_source_metadata")
        for value in sorted({str(item) for item in db_pixiv_ids or [] if normalize_text(item)})
    ]
    reasons: list[str] = []
    if filename_matches:
        reasons.append("filename_pixiv_id_pattern")
    if source_matches:
        reasons.append("source_pixiv_id_pattern")
    if PIXIV_MARKER_RE.search(normalize_text(row.get("filename"))):
        reasons.append("filename_pixiv_marker")
    if PIXIV_MARKER_RE.search(normalize_text(row.get("source"))):
        reasons.append("source_pixiv_marker")
    for category in db_signal_categories or []:
        if category not in reasons:
            reasons.append(category)
    if db_matches and "source_metadata_pixiv_work_id" not in reasons:
        reasons.append("source_metadata_pixiv_work_id")

    matches = [*filename_matches, *source_matches, *db_matches]
    work_ids = tuple(sorted({match.work_id for match in matches}))
    page_indexes = tuple(match.page_index for match in matches)
    return {
        "is_pixiv_like": bool(reasons),
        "reasons": tuple(sorted(reasons)),
        "pixiv_work_ids": work_ids,
        "pixiv_page_indexes": page_indexes,
        "ambiguous_pixiv_id": len(work_ids) > 1,
        "invalid_or_marker_only": bool(reasons) and not work_ids,
    }


def media_candidate_from_row(
    row: Mapping[str, Any],
    *,
    db_signal_categories: Sequence[str] | None = None,
    db_pixiv_ids: Sequence[str] | None = None,
    has_any_source_metadata: bool = False,
    has_pixiv_source_metadata: bool = False,
    ai_tag_count: int = 0,
    manual_locked_tag_count: int = 0,
    source_metadata_count: int = 0,
    source_concept_risk_count: int = 0,
    entity_risk_count: int = 0,
    album_count: int = 0,
) -> MediaCandidate:
    classification = classify_pixiv_like_candidate(
        row,
        db_signal_categories=db_signal_categories,
        db_pixiv_ids=db_pixiv_ids,
    )
    return MediaCandidate(
        media_id=int(row["id"]),
        filename=str(row.get("filename") or ""),
        source=str(row.get("source") or ""),
        content_class=str(row.get("content_class") or "") or None,
        file_hash=str(row.get("hash") or "") or None,
        file_size=int(row["file_size"]) if row.get("file_size") is not None else None,
        pixiv_like=bool(classification["is_pixiv_like"]),
        pixiv_work_ids=tuple(classification["pixiv_work_ids"]),
        pixiv_page_indexes=tuple(classification["pixiv_page_indexes"]),
        pixiv_prior_reasons=tuple(classification["reasons"]),
        has_any_source_metadata=has_any_source_metadata,
        has_pixiv_source_metadata=has_pixiv_source_metadata,
        ai_tag_count=ai_tag_count,
        manual_locked_tag_count=manual_locked_tag_count,
        source_metadata_count=source_metadata_count,
        source_concept_risk_count=source_concept_risk_count,
        entity_risk_count=entity_risk_count,
        album_count=album_count,
        description_present=bool(row.get("description")),
    )


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


def table_columns(conn: Connection, table_name: str) -> set[str]:
    rows = conn.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = :table_name
            """
        ),
        {"table_name": table_name},
    ).scalars()
    return {str(row) for row in rows}


def table_fingerprint(conn: Connection, table_name: str) -> dict[str, Any]:
    columns = table_columns(conn, table_name)
    fingerprint: dict[str, Any] = {"fingerprint_limited": not bool({"id", "updated_at", "created_at"} & columns)}
    if "id" in columns:
        fingerprint["max_id"] = conn.execute(text(f"SELECT MAX({qident('id')}::text) FROM {qident(table_name)}")).scalar()
    if "updated_at" in columns:
        fingerprint["max_updated_at"] = conn.execute(text(f"SELECT MAX({qident('updated_at')})::text FROM {qident(table_name)}")).scalar()
    if "created_at" in columns:
        fingerprint["max_created_at"] = conn.execute(text(f"SELECT MAX({qident('created_at')})::text FROM {qident(table_name)}")).scalar()
    if "id" in columns and "updated_at" in columns:
        fingerprint["id_updated_at_checksum"] = conn.execute(
            text(
                f"""
                SELECT md5(COALESCE(
                    string_agg(
                        {qident('id')}::text || ':' || COALESCE({qident('updated_at')}::text, ''),
                        ',' ORDER BY {qident('id')}::text
                    ),
                    ''
                ))
                FROM {qident(table_name)}
                """
            )
        ).scalar()
    return fingerprint


def build_table_state(conn: Connection) -> dict[str, Any]:
    tables = sorted(set(list_blombooru_tables(conn)) | ALLOWED_SOURCE_WRITE_TABLES | FORBIDDEN_WRITE_TABLES)
    state: dict[str, Any] = {}
    for table_name in tables:
        row = count_table(conn, table_name)
        if row.get("status") == "present":
            row["fingerprint"] = table_fingerprint(conn, table_name)
        state[table_name] = row
    return {"recorded_at": utc_now(), "tables": state}


def classify_table_mutations(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    allowed_tables: set[str] | None = None,
    forbidden_tables: set[str] | None = None,
) -> dict[str, Any]:
    allowed_tables = allowed_tables or ALLOWED_SOURCE_WRITE_TABLES
    forbidden_tables = forbidden_tables or FORBIDDEN_WRITE_TABLES
    changed: list[dict[str, Any]] = []
    before_tables = before.get("tables", {})
    after_tables = after.get("tables", {})
    for table in sorted(set(before_tables) | set(after_tables)):
        left = before_tables.get(table, {})
        right = after_tables.get(table, {})
        if left.get("status") == "missing_table" or right.get("status") == "missing_table":
            continue
        if left.get("count") != right.get("count") or left.get("fingerprint") != right.get("fingerprint"):
            changed.append(
                {
                    "table": table,
                    "before": left.get("count"),
                    "after": right.get("count"),
                    "delta": (right.get("count") or 0) - (left.get("count") or 0),
                    "fingerprint_changed": left.get("fingerprint") != right.get("fingerprint"),
                }
            )
    expected = [row for row in changed if row["table"] in allowed_tables]
    forbidden = [row for row in changed if row["table"] in forbidden_tables]
    unexpected = [row for row in changed if row["table"] not in allowed_tables and row["table"] not in forbidden_tables]
    return {
        "changed_tables": changed,
        "expected_changed_tables": expected,
        "expected_changed_table_names": [row["table"] for row in expected],
        "forbidden_changed_tables": forbidden,
        "forbidden_changed_table_names": [row["table"] for row in forbidden],
        "unexpected_changed_tables": unexpected,
        "unexpected_changed_table_names": [row["table"] for row in unexpected],
        "passed": not forbidden and not unexpected,
        "allowed_tables": sorted(allowed_tables),
        "forbidden_tables": sorted(forbidden_tables),
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
        "violet_env": str(identity.get("violet_env") or os.getenv("VIOLET_ENV", "development") or "development"),
        "git_branch": git_value(["branch", "--show-current"]),
        "git_sha": git_value(["rev-parse", "HEAD"]),
        "python_executable": str(Path(sys.executable)),
        "python_executable_path_recorded_private_only": True,
    }


def build_database_engine() -> tuple[Engine, dict[str, Any]]:
    url, identity = p0.build_database_url()
    if str(identity.get("violet_env") or "").casefold() != "development":
        raise PX1BlockedError("PX1 must run against VIOLET_ENV=development.")
    if str(identity.get("database") or "") != "blombooru":
        raise PX1BlockedError(f"PX1 must run against development DB 'blombooru', got {identity.get('database')!r}.")
    engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 10})
    return engine, identity


def assert_branch() -> None:
    branch = git_value(["branch", "--show-current"])
    if branch != BRANCH:
        raise PX1BlockedError(f"wrong_branch:{branch}; expected {BRANCH}")


def source_metadata_media_ids(conn: Connection, *, provider: str | None = None) -> set[int]:
    return p0.source_metadata_media_ids(conn, provider=provider)


def source_metadata_pixiv_ids_by_media(conn: Connection) -> dict[int, set[str]]:
    return p0.source_metadata_pixiv_ids_by_media(conn)


def pixiv_source_layer_media_ids(conn: Connection) -> dict[str, set[int]]:
    return p0.pixiv_source_layer_media_ids(conn)


def count_by_media(conn: Connection, sql: str) -> dict[int, int]:
    result: dict[int, int] = defaultdict(int)
    for row in rows_dict(conn, sql):
        if row.get("media_id") is not None:
            result[int(row["media_id"])] = int(row.get("count") or 0)
    return result


def media_id_sets_for_attached_data(conn: Connection) -> dict[str, dict[int, int]]:
    counts: dict[str, dict[int, int]] = {}
    counts["ai_tags"] = (
        count_by_media(
            conn,
            f"""
            SELECT media_id, COUNT(*) AS count
            FROM blombooru_media_tags
            WHERE {p0.ai_tag_predicate()}
            GROUP BY media_id
            """,
        )
        if table_exists(conn, "blombooru_media_tags")
        else {}
    )
    counts["manual_locked_tags"] = (
        count_by_media(
            conn,
            """
            SELECT media_id, COUNT(*) AS count
            FROM blombooru_media_tags
            WHERE COALESCE(is_locked, false) IS TRUE
               OR LOWER(COALESCE(source, 'manual')) IN ('manual', 'imported', 'trusted_external')
            GROUP BY media_id
            """,
        )
        if table_exists(conn, "blombooru_media_tags")
        else {}
    )
    counts["source_metadata"] = (
        count_by_media(
            conn,
            """
            SELECT media_id, COUNT(*) AS count
            FROM blombooru_source_metadata_records
            WHERE media_id IS NOT NULL
            GROUP BY media_id
            """,
        )
        if table_exists(conn, "blombooru_source_metadata_records")
        else {}
    )
    counts["source_concept_risk"] = {}
    if table_exists(conn, "blombooru_source_concept_signals"):
        counts["source_concept_risk"].update(
            count_by_media(
                conn,
                """
                SELECT media_id, COUNT(*) AS count
                FROM blombooru_source_concept_signals
                WHERE media_id IS NOT NULL
                GROUP BY media_id
                """,
            )
        )
    if table_exists(conn, "blombooru_source_concept_evidence"):
        for media_id, value in count_by_media(
            conn,
            """
            SELECT media_id, COUNT(*) AS count
            FROM blombooru_source_concept_evidence
            WHERE media_id IS NOT NULL
            GROUP BY media_id
            """,
        ).items():
            counts["source_concept_risk"][media_id] = counts["source_concept_risk"].get(media_id, 0) + value
    counts["entity_risk"] = {}
    for table_name in (
        "blombooru_media_entity_candidates",
        "blombooru_media_entity_assignments",
    ):
        if table_exists(conn, table_name):
            for media_id, value in count_by_media(
                conn,
                f"""
                SELECT media_id, COUNT(*) AS count
                FROM {qident(table_name)}
                WHERE media_id IS NOT NULL
                GROUP BY media_id
                """,
            ).items():
                counts["entity_risk"][media_id] = counts["entity_risk"].get(media_id, 0) + value
    counts["albums"] = (
        count_by_media(
            conn,
            """
            SELECT media_id, COUNT(*) AS count
            FROM blombooru_album_media
            GROUP BY media_id
            """,
        )
        if table_exists(conn, "blombooru_album_media")
        else {}
    )
    return counts


def load_media_candidates(conn: Connection) -> list[MediaCandidate]:
    if not table_exists(conn, "blombooru_media"):
        return []
    rows = rows_dict(
        conn,
        """
        SELECT id, filename, source, content_class, hash, file_size, description
        FROM blombooru_media
        ORDER BY id ASC
        """,
    )
    source_metadata_ids = source_metadata_media_ids(conn)
    pixiv_metadata_ids = source_metadata_media_ids(conn, provider="pixiv")
    pixiv_ids_by_media = source_metadata_pixiv_ids_by_media(conn)
    pixiv_categories = pixiv_source_layer_media_ids(conn)
    attached = media_id_sets_for_attached_data(conn)
    candidates: list[MediaCandidate] = []
    for row in rows:
        media_id = int(row["id"])
        signal_categories = [category for category, ids in pixiv_categories.items() if media_id in ids]
        candidates.append(
            media_candidate_from_row(
                row,
                db_signal_categories=signal_categories,
                db_pixiv_ids=sorted(pixiv_ids_by_media.get(media_id, set())),
                has_any_source_metadata=media_id in source_metadata_ids,
                has_pixiv_source_metadata=media_id in pixiv_metadata_ids,
                ai_tag_count=attached["ai_tags"].get(media_id, 0),
                manual_locked_tag_count=attached["manual_locked_tags"].get(media_id, 0),
                source_metadata_count=attached["source_metadata"].get(media_id, 0),
                source_concept_risk_count=attached["source_concept_risk"].get(media_id, 0),
                entity_risk_count=attached["entity_risk"].get(media_id, 0),
                album_count=attached["albums"].get(media_id, 0),
            )
        )
    return candidates


def group_exact_duplicates(candidates: Sequence[MediaCandidate]) -> list[list[MediaCandidate]]:
    groups: dict[str, list[MediaCandidate]] = defaultdict(list)
    for candidate in candidates:
        if candidate.file_hash:
            groups[candidate.file_hash].append(candidate)
    return [sorted(group, key=lambda item: item.media_id) for group in groups.values() if len(group) > 1]


def _retention_score(member: MediaCandidate) -> tuple[int, int, int, int]:
    return (
        1 if member.pixiv_like else 0,
        1 if member.has_pixiv_source_metadata or member.has_any_source_metadata else 0,
        1 if member.reliable_pixiv_prior else 0,
        -member.media_id,
    )


def _member_tag_sets(conn: Connection, media_ids: Sequence[int]) -> dict[int, set[int]]:
    if not media_ids or not table_exists(conn, "blombooru_media_tags"):
        return {media_id: set() for media_id in media_ids}
    query = text(
        """
        SELECT media_id, tag_id
        FROM blombooru_media_tags
        WHERE media_id IN :media_ids
          AND (COALESCE(is_locked, false) IS TRUE
               OR LOWER(COALESCE(source, 'manual')) IN ('manual', 'imported', 'trusted_external'))
        """
    ).bindparams(bindparam("media_ids", expanding=True))
    rows = conn.execute(
        query,
        {"media_ids": list(media_ids)},
    ).mappings()
    result: dict[int, set[int]] = {media_id: set() for media_id in media_ids}
    for row in rows:
        result[int(row["media_id"])].add(int(row["tag_id"]))
    return result


def duplicate_group_plan(
    group: Sequence[MediaCandidate],
    *,
    group_index: int,
    tag_sets: Mapping[int, set[int]] | None = None,
) -> dict[str, Any]:
    members = sorted(group, key=lambda item: item.media_id)
    retained = sorted(members, key=lambda item: (_retention_score(item), -item.media_id), reverse=True)[0]
    would_delete = [member for member in members if member.media_id != retained.media_id]
    pixiv_work_ids = sorted({work_id for member in members for work_id in member.pixiv_work_ids})
    conflicting_pixiv_ids = len(pixiv_work_ids) > 1
    file_sizes = sorted({member.file_size for member in members if member.file_size is not None})
    risk_reasons: list[str] = []
    tag_sets = tag_sets or {}
    retained_tags = set(tag_sets.get(retained.media_id, set()))
    for member in would_delete:
        unique_tags = set(tag_sets.get(member.media_id, set())) - retained_tags
        if unique_tags:
            risk_reasons.append("would_delete_has_unique_manual_or_locked_tags")
        if member.source_metadata_count:
            risk_reasons.append("would_delete_has_source_metadata")
        if member.source_concept_risk_count:
            risk_reasons.append("would_delete_has_source_concept_evidence")
        if member.entity_risk_count:
            risk_reasons.append("would_delete_has_entity_candidate_or_assignment")
        if member.album_count:
            risk_reasons.append("would_delete_has_album_membership")
        if member.description_present:
            risk_reasons.append("would_delete_has_description")
    if conflicting_pixiv_ids:
        risk_reasons.append("conflicting_pixiv_work_ids")
    if len(file_sizes) > 1:
        risk_reasons.append("same_hash_inconsistent_file_size")
    risk_reasons = sorted(set(risk_reasons))
    if conflicting_pixiv_ids or risk_reasons:
        eligibility = "needs_manual_review" if not any(reason.startswith("would_delete_has_entity") for reason in risk_reasons) else "blocked_from_auto_delete"
    else:
        eligibility = "auto_delete_candidate"
    return {
        "group_id": f"exact_hash_group_{group_index:05d}",
        "hash_private_ref": stable_private_id(members[0].file_hash or "", "hash"),
        "duplicate_count": len(members),
        "media_ids_private": [member.media_id for member in members],
        "media_private_refs": [stable_private_id(member.media_id, "media") for member in members],
        "source_labels": sorted({"pixiv_like" if member.pixiv_like else "non_pixiv_or_unknown" for member in members}),
        "pixiv_like_flags": {str(member.media_id): member.pixiv_like for member in members},
        "existing_source_metadata_flags": {str(member.media_id): member.has_any_source_metadata for member in members},
        "ai_tag_count_summary": {
            "min": min(member.ai_tag_count for member in members),
            "max": max(member.ai_tag_count for member in members),
            "total": sum(member.ai_tag_count for member in members),
        },
        "manual_locked_tag_risk": any(member.manual_locked_tag_count for member in would_delete),
        "source_metadata_risk": any(member.source_metadata_count for member in would_delete),
        "source_concept_entity_truth_risk": any(member.source_concept_risk_count or member.entity_risk_count for member in would_delete),
        "retained_media_candidate": retained.media_id,
        "retained_media_private_ref": stable_private_id(retained.media_id, "media"),
        "would_delete_media_candidates": [member.media_id for member in would_delete],
        "would_delete_private_refs": [stable_private_id(member.media_id, "media") for member in would_delete],
        "retention_reason": "pixiv_like_or_source_metadata_preferred" if retained.pixiv_like else "deterministic_lowest_media_id",
        "deletion_eligibility": eligibility,
        "auto_delete_candidate": eligibility == "auto_delete_candidate",
        "needs_manual_review": eligibility == "needs_manual_review",
        "blocked_from_auto_delete": eligibility == "blocked_from_auto_delete",
        "risk_reasons": risk_reasons,
        "conflicting_pixiv_work_ids": conflicting_pixiv_ids,
        "estimated_reclaim_bytes_private": sum(member.file_size or 0 for member in would_delete),
        "execution_allowed_in_px1": False,
    }


def provider_execution_exclusion_reason(
    candidate: MediaCandidate,
    would_delete_ids: set[int],
    manual_review_group_members: set[int],
) -> str | None:
    if not candidate.pixiv_like:
        return "not_pixiv_like"
    content_class = str(candidate.content_class or "").casefold()
    if content_class == "unknown":
        return "unknown_excluded_from_provider_execution"
    if content_class != "anime":
        return "non_anime_excluded_from_provider_execution"
    if candidate.has_any_source_metadata:
        return "already_has_source_metadata"
    if not candidate.primary_work_id:
        return "missing_pixiv_work_id"
    if len(candidate.pixiv_work_ids) != 1 or not candidate.reliable_pixiv_prior:
        return "unreliable_or_ambiguous_pixiv_work_id"
    if candidate.media_id in would_delete_ids:
        return "would_delete_exact_duplicate"
    if candidate.media_id in manual_review_group_members:
        return "duplicate_group_needs_manual_review"
    return None


def build_duplicate_dry_run(conn: Connection, candidates: Sequence[MediaCandidate]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    groups = group_exact_duplicates(candidates)
    plans: list[dict[str, Any]] = []
    for index, group in enumerate(groups, start=1):
        tag_sets = _member_tag_sets(conn, [member.media_id for member in group])
        plans.append(duplicate_group_plan(group, group_index=index, tag_sets=tag_sets))
    involved = sum(plan["duplicate_count"] for plan in plans)
    would_delete = sum(len(plan["would_delete_media_candidates"]) for plan in plans)
    summary = {
        "exact_duplicate_groups": len(plans),
        "total_duplicate_media_involved": involved,
        "would_delete_count_if_later_approved": would_delete,
        "groups_with_pixiv_like_retention": sum(1 for plan in plans if plan["retention_reason"] == "pixiv_like_or_source_metadata_preferred"),
        "all_non_pixiv_groups": sum(1 for plan in plans if "pixiv_like" not in plan["source_labels"]),
        "ambiguous_conflicting_pixiv_groups": sum(1 for plan in plans if plan["conflicting_pixiv_work_ids"]),
        "groups_blocked_by_attached_data_risk": sum(
            1
            for plan in plans
            if any(reason.startswith("would_delete_has_") for reason in plan["risk_reasons"])
        ),
        "auto_delete_candidate_groups_if_later_approved": sum(1 for plan in plans if plan["auto_delete_candidate"]),
        "estimated_storage_reclaim_bytes_if_safely_computable": sum(plan["estimated_reclaim_bytes_private"] for plan in plans),
        "deletion_executed": False,
        "db_mutated_for_dedup": False,
        "files_mutated_for_dedup": False,
        "policy": "exact hash only; deletion is deferred to a later explicitly approved destructive phase",
    }
    return plans, summary


def build_pixiv_inventory(
    baseline: Mapping[str, Any],
    candidates: Sequence[MediaCandidate],
    duplicate_summary: Mapping[str, Any],
    duplicate_plans: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    pixiv_candidates = [candidate for candidate in candidates if candidate.pixiv_like]
    reason_counter: Counter[str] = Counter()
    work_page_counter: Counter[tuple[str, int]] = Counter()
    for candidate in pixiv_candidates:
        reason_counter.update(candidate.pixiv_prior_reasons)
        if candidate.primary_work_id:
            work_page_counter[(candidate.primary_work_id, candidate.primary_page_index)] += 1
    would_delete_ids = {
        int(media_id)
        for plan in duplicate_plans
        for media_id in plan.get("would_delete_media_candidates", [])
    }
    manual_review_group_members = {
        int(media_id)
        for plan in duplicate_plans
        if plan.get("needs_manual_review") or plan.get("blocked_from_auto_delete")
        for media_id in [*plan.get("would_delete_media_candidates", []), plan.get("retained_media_candidate")]
        if media_id is not None
    }
    local_backlog_candidates = [
        candidate
        for candidate in pixiv_candidates
        if candidate.eligible
        and not candidate.has_any_source_metadata
        and candidate.primary_work_id
        and len(candidate.pixiv_work_ids) == 1
        and candidate.media_id not in would_delete_ids
    ]
    excluded_reasons = Counter()
    provider_excluded_reasons = Counter()
    for candidate in pixiv_candidates:
        if not candidate.eligible:
            excluded_reasons["ineligible_content_class"] += 1
        elif candidate.has_any_source_metadata:
            excluded_reasons["already_has_source_metadata"] += 1
        elif not candidate.primary_work_id:
            excluded_reasons["missing_pixiv_work_id"] += 1
        elif len(candidate.pixiv_work_ids) > 1:
            excluded_reasons["ambiguous_pixiv_work_id"] += 1
        elif candidate.media_id in would_delete_ids:
            excluded_reasons["would_delete_exact_duplicate"] += 1
        provider_reason = provider_execution_exclusion_reason(candidate, would_delete_ids, manual_review_group_members)
        if provider_reason:
            provider_excluded_reasons[provider_reason] += 1
    provider_execution_eligible = [
        candidate
        for candidate in pixiv_candidates
        if provider_execution_exclusion_reason(candidate, would_delete_ids, manual_review_group_members) is None
    ]
    return {
        "method": "DB-derived signals only; no source root scan, import, classification, AI tagging, or SourceConcept resolver.",
        "total_media": baseline.get("total_media"),
        "eligible_media": baseline.get("eligible_media"),
        "pixiv_like_media_candidates": len(pixiv_candidates),
        "pixiv_like_candidates_with_existing_source_metadata": sum(1 for item in pixiv_candidates if item.has_any_source_metadata),
        "pixiv_like_candidates_without_source_metadata": sum(1 for item in pixiv_candidates if not item.has_any_source_metadata),
        "distinct_pixiv_work_ids": len({item.primary_work_id for item in pixiv_candidates if item.primary_work_id}),
        "duplicate_pixiv_work_page_candidates": sum(1 for value in work_page_counter.values() if value > 1),
        "invalid_or_ambiguous_pixiv_id_candidates": sum(1 for item in pixiv_candidates if not item.primary_work_id or len(item.pixiv_work_ids) > 1),
        "local_backlog_candidates_before_provider_privacy_gate": len(local_backlog_candidates),
        "candidates_eligible_for_metadata_extraction": len(provider_execution_eligible),
        "provider_execution_eligible_anime_candidates": len(provider_execution_eligible),
        "provider_execution_excluded_unknown": provider_excluded_reasons.get("unknown_excluded_from_provider_execution", 0),
        "provider_execution_excluded_non_anime": provider_excluded_reasons.get("non_anime_excluded_from_provider_execution", 0),
        "provider_execution_excluded_already_has_source_metadata": provider_excluded_reasons.get("already_has_source_metadata", 0),
        "candidates_excluded_from_metadata_extraction": len(pixiv_candidates) - len(provider_execution_eligible),
        "exclusion_reason_counts": dict(sorted(excluded_reasons.items())),
        "provider_execution_exclusion_reason_counts": dict(sorted(provider_excluded_reasons.items())),
        "candidates_that_are_exact_duplicate_would_delete": sum(1 for item in pixiv_candidates if item.media_id in would_delete_ids),
        "reason_category_counts": dict(sorted(reason_counter.items())),
        "duplicate_dry_run_available_before_selection": True,
        "duplicate_summary": dict(duplicate_summary),
        "private_candidate_count": len(pixiv_candidates),
        "private_candidate_rows_public": False,
        "exact_media_ids_public": False,
        "exact_pixiv_ids_public": False,
    }


def candidate_private_row(candidate: MediaCandidate) -> dict[str, Any]:
    return {
        "media_id": candidate.media_id,
        "private_media_ref": stable_private_id(candidate.media_id, "media"),
        "filename_private": candidate.filename,
        "source_private": candidate.source,
        "content_class": candidate.content_class,
        "hash_private_ref": stable_private_id(candidate.file_hash or "", "hash") if candidate.file_hash else None,
        "file_size": candidate.file_size,
        "pixiv_like": candidate.pixiv_like,
        "pixiv_work_ids_private": list(candidate.pixiv_work_ids),
        "pixiv_page_indexes_private": list(candidate.pixiv_page_indexes),
        "pixiv_prior_reasons": list(candidate.pixiv_prior_reasons),
        "has_any_source_metadata": candidate.has_any_source_metadata,
        "has_pixiv_source_metadata": candidate.has_pixiv_source_metadata,
        "eligible": candidate.eligible,
        "provider_execution_eligible_without_duplicate_context": candidate.provider_execution_eligible,
        "ai_tag_count": candidate.ai_tag_count,
        "manual_locked_tag_count": candidate.manual_locked_tag_count,
        "source_metadata_count": candidate.source_metadata_count,
        "source_concept_risk_count": candidate.source_concept_risk_count,
        "entity_risk_count": candidate.entity_risk_count,
        "album_count": candidate.album_count,
    }


def selection_priority(candidate: MediaCandidate, would_delete_ids: set[int]) -> tuple[Any, ...]:
    return (
        0 if candidate.media_id not in would_delete_ids else 1,
        0 if candidate.reliable_pixiv_prior else 1,
        0 if candidate.has_any_source_metadata is False else 1,
        candidate.media_id,
    )


def select_metadata_targets(
    candidates: Sequence[MediaCandidate],
    duplicate_plans: Sequence[Mapping[str, Any]],
    *,
    limit: int,
) -> tuple[list[MediaCandidate], dict[str, Any]]:
    would_delete_ids = {
        int(media_id)
        for plan in duplicate_plans
        for media_id in plan.get("would_delete_media_candidates", [])
    }
    manual_review_group_members = {
        int(media_id)
        for plan in duplicate_plans
        if plan.get("needs_manual_review") or plan.get("blocked_from_auto_delete")
        for media_id in [*plan.get("would_delete_media_candidates", []), plan.get("retained_media_candidate")]
        if media_id is not None
    }
    eligible: list[MediaCandidate] = []
    excluded = Counter()
    for candidate in candidates:
        if not candidate.pixiv_like:
            continue
        reason = provider_execution_exclusion_reason(candidate, would_delete_ids, manual_review_group_members)
        if reason:
            excluded[reason] += 1
            continue
        eligible.append(candidate)
    eligible.sort(key=lambda item: selection_priority(item, would_delete_ids))
    selected = eligible[: max(limit, 0)]
    summary = {
        "default_limit": 500,
        "requested_limit": limit,
        "eligible_before_limit": len(eligible),
        "selected_count": len(selected),
        "excluded_reason_counts": dict(sorted(excluded.items())),
        "provider_execution_policy": "anime_only_source_metadata_missing_reliable_single_pixiv_filename_prior",
        "provider_execution_eligible_anime_candidates": len(eligible),
        "unknown_excluded_from_provider_execution": excluded.get("unknown_excluded_from_provider_execution", 0),
        "non_anime_excluded_from_provider_execution": excluded.get("non_anime_excluded_from_provider_execution", 0),
        "already_has_source_metadata_excluded": excluded.get("already_has_source_metadata", 0),
        "would_delete_candidates_excluded": excluded.get("would_delete_exact_duplicate", 0),
        "manual_review_candidates_excluded": excluded.get("duplicate_group_needs_manual_review", 0),
        "exact_media_ids_public": False,
        "exact_pixiv_ids_public": False,
        "selection_policy": [
            "exclude would-delete candidates from exact duplicate dry-run",
            "exclude manual-review duplicate groups",
            "exclude unknown/non-anime content from provider execution",
            "prefer reliable filename Pixiv ID/page priors",
            "exclude candidates with existing source metadata",
            "respect bounded metadata limit",
        ],
    }
    return selected, summary


def split_operator_command(command: str) -> tuple[str, ...]:
    parts = shlex.split(command, posix=os.name != "nt")
    return tuple(part.strip().strip("\"'") for part in parts if part.strip().strip("\"'"))


def probe_gallery_dl_entrypoint(
    explicit_command: str | None = None,
    *,
    runner: CompletedRunner = subprocess.run,
    python_executable: str | None = None,
) -> GalleryDlEntrypoint:
    attempts: list[tuple[str, tuple[str, ...]]] = []
    if explicit_command:
        attempts.append(("explicit_operator_command", split_operator_command(explicit_command)))
    else:
        attempts.append(("project_python_module", (python_executable or sys.executable, "-m", "gallery_dl")))
        external = shutil.which("gallery-dl")
        if external:
            attempts.append(("external_executable", (external,)))
    for mode, command in attempts:
        if not command:
            continue
        try:
            completed = runner(
                [*command, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                shell=False,
            )
        except (FileNotFoundError, subprocess.SubprocessError) as exc:
            last_error = exc.__class__.__name__
            continue
        if completed.returncode == 0:
            version = (completed.stdout or completed.stderr or "").strip().splitlines()
            return GalleryDlEntrypoint(True, mode, command, version[0] if version else None)
        last_error = (completed.stderr or completed.stdout or "gallery-dl version probe failed").strip()[:200]
    return GalleryDlEntrypoint(False, "unavailable", error=locals().get("last_error", "gallery-dl not found"))


def build_gallery_dl_metadata_command(
    entrypoint: GalleryDlEntrypoint,
    work_id: str,
    *,
    sleep_request_seconds: float = 2.0,
) -> list[str]:
    if not entrypoint.available or not entrypoint.command:
        raise PX1BlockedError("gallery_dl_entrypoint_unavailable")
    command = [
        *entrypoint.command,
        "--dump-json",
        "--no-download",
        "--sleep-request",
        str(sleep_request_seconds),
        f"https://www.pixiv.net/artworks/{work_id}",
    ]
    forbidden = {"-D", "--dest", "--directory", "--download-archive", "--range"}
    if any(part in forbidden for part in command):
        raise PX1BlockedError("provider_command_contains_download_or_directory_option")
    return command


def classify_provider_failure(stderr: str, stdout: str = "") -> str:
    text_value = f"{stderr}\n{stdout}"
    if RATE_LIMIT_RE.search(text_value):
        return "rate_limited"
    if UNAVAILABLE_RE.search(text_value):
        return "unavailable_private_or_deleted"
    if AUTH_ERROR_RE.search(text_value):
        return "auth_or_config_failure"
    if COMMAND_OPTION_RE.search(text_value):
        return "command_option_issue"
    return "provider_request_failed"


def _json_rows_from_stdout(stdout: str) -> list[Any]:
    rows: list[Any] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not rows and stdout.strip():
        try:
            value = json.loads(stdout)
            rows.append(value)
        except json.JSONDecodeError:
            pass
    return rows


def _looks_like_gallery_metadata_record(value: Mapping[str, Any]) -> bool:
    return any(
        key in value
        for key in (
            "id",
            "illust_id",
            "work_id",
            "num",
            "page_index",
            "title",
            "caption",
            "description",
            "user",
            "user_name",
            "artist",
            "tags",
            "tag",
            "keywords",
        )
    )


def _metadata_dicts_from_gallery_dl(stdout: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    def visit(row: Any) -> None:
        if isinstance(row, Mapping):
            if _looks_like_gallery_metadata_record(row):
                records.append(dict(row))
            return
        if isinstance(row, list) and row:
            if len(row) >= 3 and isinstance(row[2], Mapping):
                visit(row[2])
                return
            if len(row) >= 2 and isinstance(row[1], Mapping):
                visit(row[1])
                return
            for item in row:
                visit(item)

    for row in _json_rows_from_stdout(stdout):
        visit(row)
    return records


def _gallery_dl_error_events(stdout: str) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []

    def visit(row: Any) -> None:
        if (
            isinstance(row, list)
            and len(row) >= 2
            and row[0] == -1
            and isinstance(row[1], Mapping)
        ):
            payload = row[1]
            events.append(
                {
                    "error": normalize_text(payload.get("error")) or "unknown",
                    "message": normalize_text(payload.get("message")) or "",
                }
            )
            return
        if isinstance(row, list):
            for item in row:
                visit(item)

    for row in _json_rows_from_stdout(stdout):
        visit(row)
    return events


def _provider_error_public_type(stdout: str) -> str | None:
    for event in _gallery_dl_error_events(stdout):
        error = normalize_text(event.get("error"))
        if error:
            return error[:80]
    return None


def raw_cache_paths(raw_dir: Path, candidate: MediaCandidate) -> dict[str, Path]:
    work_id = re.sub(r"[^A-Za-z0-9_-]+", "_", candidate.primary_work_id or "missing")
    stem = f"provider-pixiv-work-{work_id}-p{candidate.primary_page_index}-media-{candidate.media_id}"
    return {
        "stdout": raw_dir / f"{stem}.stdout.jsonl",
        "stderr": raw_dir / f"{stem}.stderr.txt",
        "diagnostics": raw_dir / f"{stem}.diagnostics.json",
    }


def completed_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def json_type_name(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


def classify_provider_output_diagnostic(stdout: str, stderr: str, failure_reason: str) -> str:
    combined = f"{stderr}\n{stdout}"
    provider_errors = _gallery_dl_error_events(stdout)
    provider_error_text = "\n".join(
        f"{event.get('error', '')}\n{event.get('message', '')}" for event in provider_errors
    )
    if RATE_LIMIT_RE.search(combined):
        return "rate_limited"
    if COMMAND_OPTION_RE.search(combined):
        return "command_option_issue"
    if UNAVAILABLE_RE.search(f"{provider_error_text}\n{combined}"):
        return "unavailable_private_or_deleted"
    if AUTH_ERROR_RE.search(f"{provider_error_text}\n{combined}"):
        return "auth_or_config_failure"
    if provider_errors:
        return "provider_error_event"
    if failure_reason in {"no_metadata_records", "cache_no_metadata_records"}:
        if not stdout.strip():
            return "stdout_empty"
        rows = _json_rows_from_stdout(stdout)
        if not rows:
            return "stdout_nonempty_unparsed"
        return "json_shape_unsupported"
    if failure_reason in {"metadata_normalization_failed", "cache_parse_failure"}:
        return "parser_mismatch"
    return "other_provider_failure"


def provider_output_shape(stdout: str, stderr: str, failure_reason: str) -> dict[str, Any]:
    rows = _json_rows_from_stdout(stdout)
    first_json = rows[0] if rows else None
    provider_error_type = _provider_error_public_type(stdout)
    return {
        "stdout_empty": not bool(stdout.strip()),
        "stdout_line_count": len(stdout.splitlines()),
        "json_line_count": len(rows),
        "first_json_type": json_type_name(first_json),
        "provider_error_type": provider_error_type,
        "provider_error_present": provider_error_type is not None,
        "stderr_present": bool(stderr.strip()),
        "stderr_line_count": len(stderr.splitlines()),
        "failure_reason": failure_reason,
        "diagnostic_class": classify_provider_output_diagnostic(stdout, stderr, failure_reason),
    }


def write_raw_provider_diagnostics(
    paths: Mapping[str, Path],
    *,
    stdout: str,
    stderr: str,
    diagnostics: Mapping[str, Any],
) -> None:
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    paths["stdout"].write_text(stdout, encoding="utf-8", newline="\n")
    paths["stderr"].write_text(stderr, encoding="utf-8", newline="\n")
    write_json(paths["diagnostics"], dict(diagnostics))


def raw_ref_fields(paths: Mapping[str, Path]) -> dict[str, str]:
    return {
        "raw_stdout_private_ref": str(paths["stdout"]),
        "raw_stderr_private_ref": str(paths["stderr"]),
        "raw_diagnostics_private_ref": str(paths["diagnostics"]),
    }


def provider_output_diagnosis_summary(failure_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    shapes = [dict(row.get("public_provider_output_shape") or {}) for row in failure_rows]
    diagnostic_counts = Counter(str(shape.get("diagnostic_class") or "unknown") for shape in shapes)
    failure_reason_counts = Counter(str(row.get("failure_reason") or "unknown") for row in failure_rows)
    provider_error_type_counts = Counter(
        str(shape.get("provider_error_type"))
        for shape in shapes
        if shape.get("provider_error_type")
    )
    return {
        "failure_reason_counts": dict(sorted(failure_reason_counts.items())),
        "diagnostic_class_counts": dict(sorted(diagnostic_counts.items())),
        "provider_error_event_count": sum(1 for shape in shapes if shape.get("provider_error_present")),
        "provider_error_type_counts": dict(sorted(provider_error_type_counts.items())),
        "stdout_empty_count": diagnostic_counts.get("stdout_empty", 0),
        "stdout_nonempty_unparsed_count": diagnostic_counts.get("stdout_nonempty_unparsed", 0),
        "parser_mismatch_count": diagnostic_counts.get("parser_mismatch", 0) + diagnostic_counts.get("json_shape_unsupported", 0),
        "auth_config_failure_count": diagnostic_counts.get("auth_or_config_failure", 0),
        "rate_limited_count": diagnostic_counts.get("rate_limited", 0),
        "unavailable_private_deleted_count": diagnostic_counts.get("unavailable_private_or_deleted", 0),
        "command_option_issue_count": diagnostic_counts.get("command_option_issue", 0),
        "other_provider_failure_count": diagnostic_counts.get("other_provider_failure", 0),
        "no_metadata_records_count": sum(
            1
            for row in failure_rows
            if row.get("failure_reason") in {"no_metadata_records", "cache_no_metadata_records"}
        ),
        "public_shapes": shapes[:10],
        "raw_stdout_stderr_public": False,
    }


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if normalize_text(value) else []
    if isinstance(value, Mapping):
        text_value = value.get("name") or value.get("tag") or value.get("label") or value.get("value")
        return [normalize_text(text_value)] if normalize_text(text_value) else []
    if isinstance(value, Iterable):
        result: list[str] = []
        for item in value:
            result.extend(_as_text_list(item))
        return result
    return []


def normalize_gallery_dl_metadata(
    records: Sequence[Mapping[str, Any]],
    *,
    candidate: MediaCandidate,
    raw_stdout_path: Path | None,
) -> dict[str, Any] | None:
    if not records:
        return None
    preferred: Mapping[str, Any] = {}
    for record in records:
        record_id = str(record.get("id") or record.get("illust_id") or record.get("work_id") or "")
        record_num = record.get("num", record.get("page_index", record.get("page")))
        if record_id == candidate.primary_work_id and (record_num is None or int(record_num or 0) == candidate.primary_page_index):
            preferred = record
            break
    if not preferred:
        preferred = records[0]
    work_id = str(preferred.get("id") or preferred.get("illust_id") or preferred.get("work_id") or candidate.primary_work_id or "")
    page_index_raw = preferred.get("num", preferred.get("page_index", preferred.get("page", candidate.primary_page_index)))
    try:
        page_index = int(page_index_raw or 0)
    except (TypeError, ValueError):
        page_index = candidate.primary_page_index
    user = preferred.get("user") if isinstance(preferred.get("user"), Mapping) else {}
    artist_name = (
        preferred.get("user_name")
        or preferred.get("artist")
        or preferred.get("artist_name")
        or preferred.get("author")
        or user.get("name")
        or user.get("account")
    )
    artist_id = preferred.get("user_id") or preferred.get("artist_id") or user.get("id")
    tags = _as_text_list(preferred.get("tags") or preferred.get("tag") or preferred.get("keywords"))
    translated_tags = _as_text_list(preferred.get("translated_tags") or preferred.get("tag_translations"))
    title = normalize_text(preferred.get("title"))
    caption = normalize_text(preferred.get("caption") or preferred.get("description") or preferred.get("commentary"))
    return {
        "media_id": candidate.media_id,
        "work_id": work_id,
        "page_index": page_index,
        "page_count": preferred.get("page_count") or preferred.get("count"),
        "title": title or None,
        "artist_name": normalize_text(artist_name) or None,
        "artist_id": str(artist_id) if artist_id not in (None, "") else None,
        "tags": tags,
        "translated_tags": translated_tags,
        "caption": caption or None,
        "source_url": f"https://www.pixiv.net/artworks/{work_id}" if work_id else None,
        "raw_metadata_json": dict(preferred),
        "raw_stdout_private_path": str(raw_stdout_path) if raw_stdout_path else None,
        "metadata_richness": "rich_structured_metadata" if (title and (artist_name or tags)) else "partial_structured_metadata",
    }


def run_single_metadata_request(
    candidate: MediaCandidate,
    entrypoint: GalleryDlEntrypoint,
    *,
    raw_dir: Path,
    timeout: int,
    sleep_request_seconds: float,
    runner: CompletedRunner = subprocess.run,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    command = build_gallery_dl_metadata_command(entrypoint, candidate.primary_work_id or "", sleep_request_seconds=sleep_request_seconds)
    cache_paths = raw_cache_paths(raw_dir, candidate)
    request_row = {
        "media_id": candidate.media_id,
        "private_media_ref": stable_private_id(candidate.media_id, "media"),
        "detected_pixiv_id": candidate.primary_work_id,
        "page_index": candidate.primary_page_index,
        "provider": "pixiv",
        "method": "gallery-dl --dump-json --no-download",
        "authenticated": "unknown_config_external_to_runner",
        "original_downloaded": False,
        "cache_hit": False,
        "provider_called": False,
        "retry_count": 0,
        "command_private": command,
        "public_safe_label": stable_private_id(candidate.media_id, "px1_target"),
        **raw_ref_fields(cache_paths),
    }
    if cache_paths["stdout"].exists():
        stdout = cache_paths["stdout"].read_text(encoding="utf-8", errors="replace")
        stderr = cache_paths["stderr"].read_text(encoding="utf-8", errors="replace") if cache_paths["stderr"].exists() else ""
        cached_request = {**request_row, "cache_hit": True, "provider_called": False}
        records = _metadata_dicts_from_gallery_dl(stdout)
        if not records:
            reason = "cache_no_metadata_records"
            shape = provider_output_shape(stdout, stderr, reason)
            write_raw_provider_diagnostics(cache_paths, stdout=stdout, stderr=stderr, diagnostics=shape)
            failure = {
                **cached_request,
                "metadata_request_status": "failed",
                "failure_reason": reason,
                "exit_code": None,
                "public_provider_output_shape": shape,
            }
            return cached_request, None, failure
        success = normalize_gallery_dl_metadata(records, candidate=candidate, raw_stdout_path=cache_paths["stdout"])
        if success is None:
            reason = "cache_parse_failure"
            shape = provider_output_shape(stdout, stderr, reason)
            write_raw_provider_diagnostics(cache_paths, stdout=stdout, stderr=stderr, diagnostics=shape)
            failure = {
                **cached_request,
                "metadata_request_status": "failed",
                "failure_reason": reason,
                "exit_code": None,
                "public_provider_output_shape": shape,
            }
            return cached_request, None, failure
        success_row = {
            **cached_request,
            **success,
            "metadata_request_status": "success_cache_hit",
            "failure_reason": None,
            "raw_provider_json_private_ref": str(cache_paths["stdout"]),
            "tag_count": len(success["tags"]),
            "translated_tag_count": len(success["translated_tags"]),
        }
        return cached_request, success_row, None
    provider_request = {**request_row, "cache_hit": False, "provider_called": True}
    try:
        completed = runner(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = completed_text(getattr(exc, "output", ""))
        stderr = completed_text(getattr(exc, "stderr", ""))
        reason = "provider_timeout"
        shape = provider_output_shape(stdout, stderr, reason)
        write_raw_provider_diagnostics(cache_paths, stdout=stdout, stderr=stderr, diagnostics=shape)
        failure = {
            **provider_request,
            "metadata_request_status": "failed",
            "failure_reason": reason,
            "stderr_redacted": redact_public_text(stderr)[:500],
            "public_provider_output_shape": shape,
        }
        return provider_request, None, failure
    except (OSError, subprocess.SubprocessError) as exc:
        reason = exc.__class__.__name__
        shape = provider_output_shape("", str(exc), reason)
        write_raw_provider_diagnostics(cache_paths, stdout="", stderr=str(exc), diagnostics=shape)
        failure = {
            **provider_request,
            "metadata_request_status": "failed",
            "failure_reason": reason,
            "stderr_redacted": redact_public_text(str(exc))[:500],
            "public_provider_output_shape": shape,
        }
        return provider_request, None, failure
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    write_raw_provider_diagnostics(
        cache_paths,
        stdout=stdout,
        stderr=stderr,
        diagnostics=provider_output_shape(stdout, stderr, "provider_completed"),
    )
    if completed.returncode != 0:
        reason = classify_provider_failure(completed.stderr or "", completed.stdout or "")
        shape = provider_output_shape(stdout, stderr, reason)
        write_raw_provider_diagnostics(cache_paths, stdout=stdout, stderr=stderr, diagnostics=shape)
        failure = {
            **provider_request,
            "metadata_request_status": "failed",
            "failure_reason": reason,
            "exit_code": completed.returncode,
            "stderr_redacted": redact_public_text(completed.stderr or "")[:500],
            "public_provider_output_shape": shape,
        }
        return provider_request, None, failure
    records = _metadata_dicts_from_gallery_dl(stdout)
    if not records:
        reason = "no_metadata_records"
        shape = provider_output_shape(stdout, stderr, reason)
        write_raw_provider_diagnostics(cache_paths, stdout=stdout, stderr=stderr, diagnostics=shape)
        failure = {
            **provider_request,
            "metadata_request_status": "failed",
            "failure_reason": reason,
            "exit_code": 0,
            "public_provider_output_shape": shape,
        }
        return provider_request, None, failure
    success = normalize_gallery_dl_metadata(records, candidate=candidate, raw_stdout_path=cache_paths["stdout"])
    if success is None:
        reason = "metadata_normalization_failed"
        shape = provider_output_shape(stdout, stderr, reason)
        write_raw_provider_diagnostics(cache_paths, stdout=stdout, stderr=stderr, diagnostics=shape)
        failure = {
            **provider_request,
            "metadata_request_status": "failed",
            "failure_reason": reason,
            "exit_code": 0,
            "public_provider_output_shape": shape,
        }
        return provider_request, None, failure
    success_row = {
        **provider_request,
        **success,
        "metadata_request_status": "success",
        "failure_reason": None,
        "raw_provider_json_private_ref": str(cache_paths["stdout"]),
        "tag_count": len(success["tags"]),
        "translated_tag_count": len(success["translated_tags"]),
    }
    return provider_request, success_row, None


def execute_metadata_requests(
    selected: Sequence[MediaCandidate],
    entrypoint: GalleryDlEntrypoint,
    *,
    output_dir: Path,
    timeout: int,
    sleep_request_seconds: float,
    failure_budget: ProviderFailureBudget,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    raw_dir = output_dir / "provider-cache" / "raw-gallery-dl-json"
    request_rows: list[dict[str, Any]] = []
    success_rows: list[dict[str, Any]] = []
    failure_rows: list[dict[str, Any]] = []
    for candidate in selected:
        if failure_budget.stopped:
            break
        request, success, failure = run_single_metadata_request(
            candidate,
            entrypoint,
            raw_dir=raw_dir,
            timeout=timeout,
            sleep_request_seconds=sleep_request_seconds,
        )
        request_rows.append(request)
        if success is not None:
            success_rows.append(success)
            failure_budget.record_success()
        if failure is not None:
            failure_rows.append(failure)
            failure_budget.record_failure(str(failure.get("failure_reason") or "provider_request_failed"))
        if request.get("provider_called"):
            time.sleep(max(float(sleep_request_seconds), 0.0))
    failure_reason_counts = Counter(str(row.get("failure_reason") or "unknown") for row in failure_rows)
    diagnosis = provider_output_diagnosis_summary(failure_rows)
    provider_cache_summary = {
        "filesystem_provider_cache_used": True,
        "db_provider_cache_used": False,
        "raw_json_cache_dir_private": str(raw_dir),
        "request_count": len(request_rows),
        "success_count": len(success_rows),
        "failure_count": len(failure_rows),
        "failure_reason_counts": dict(sorted(failure_reason_counts.items())),
        "cache_hit_count": sum(1 for row in request_rows if row.get("cache_hit")),
        "cache_miss_count": sum(1 for row in request_rows if not row.get("cache_hit")),
        "provider_called_count": sum(1 for row in request_rows if row.get("provider_called")),
        "cache_parse_failure_count": sum(
            1 for row in failure_rows if row.get("failure_reason") == "cache_parse_failure"
        ),
        "cache_no_metadata_records_count": sum(
            1 for row in failure_rows if row.get("failure_reason") == "cache_no_metadata_records"
        ),
        "raw_failure_artifact_count": sum(1 for row in failure_rows if row.get("raw_diagnostics_private_ref")),
        "provider_output_diagnosis": diagnosis,
        "original_downloaded": False,
        "failure_budget": failure_budget.public_dict(),
    }
    return request_rows, success_rows, failure_rows, provider_cache_summary


def language_script_hint(value: str) -> tuple[str | None, str | None]:
    text_value = normalize_text(value)
    if not text_value:
        return None, None
    has_latin = any("a" <= ch.casefold() <= "z" for ch in text_value)
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in text_value)
    has_kana = any("\u3040" <= ch <= "\u30ff" for ch in text_value)
    if has_kana:
        return "ja", "kana_or_japanese"
    if has_cjk and has_latin:
        return "mixed", "cjk_latin"
    if has_cjk:
        return "cjk", "cjk"
    if has_latin:
        return "latin", "latin"
    return None, "other"


def _json_param(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=json_default)


def _upsert_source_metadata(conn: Connection, row: Mapping[str, Any], run_id: str) -> tuple[int, str]:
    provider_record_key = f"gallery-dl-real-pixiv:metadata:{row['work_id']}:p{row['page_index']}:m{row['media_id']}"
    existing = conn.execute(
        text(
            """
            SELECT id
            FROM blombooru_source_metadata_records
            WHERE provider = 'pixiv' AND provider_record_key = :provider_record_key
            """
        ),
        {"provider_record_key": provider_record_key},
    ).scalar()
    action = "updated" if existing else "inserted"
    result = conn.execute(
        text(
            """
            INSERT INTO blombooru_source_metadata_records (
                provider, provider_run_id, run_label, provider_record_key, media_id,
                source_work_id, source_page_index, source_url, title, artist_name,
                artist_id, confidence, metadata_kind, data_type_label,
                raw_metadata_json, provenance, status, retrieved_at, updated_at
            )
            VALUES (
                'pixiv', :provider_run_id, :run_label, :provider_record_key, :media_id,
                :source_work_id, :source_page_index, :source_url, :title, :artist_name,
                :artist_id, :confidence, 'provider_metadata', 'real_live_or_local_provider_data',
                CAST(:raw_metadata_json AS jsonb), CAST(:provenance AS jsonb), 'observed', now(), now()
            )
            ON CONFLICT (provider, provider_record_key) DO UPDATE SET
                provider_run_id = EXCLUDED.provider_run_id,
                run_label = EXCLUDED.run_label,
                media_id = EXCLUDED.media_id,
                source_work_id = EXCLUDED.source_work_id,
                source_page_index = EXCLUDED.source_page_index,
                source_url = EXCLUDED.source_url,
                title = EXCLUDED.title,
                artist_name = EXCLUDED.artist_name,
                artist_id = EXCLUDED.artist_id,
                confidence = EXCLUDED.confidence,
                metadata_kind = EXCLUDED.metadata_kind,
                data_type_label = EXCLUDED.data_type_label,
                raw_metadata_json = EXCLUDED.raw_metadata_json,
                provenance = EXCLUDED.provenance,
                status = EXCLUDED.status,
                retrieved_at = EXCLUDED.retrieved_at,
                updated_at = now()
            RETURNING id
            """
        ),
        {
            "provider_run_id": run_id,
            "run_label": PHASE_SLUG,
            "provider_record_key": provider_record_key,
            "media_id": int(row["media_id"]),
            "source_work_id": str(row["work_id"]),
            "source_page_index": int(row["page_index"]),
            "source_url": row.get("source_url"),
            "title": row.get("title"),
            "artist_name": row.get("artist_name"),
            "artist_id": row.get("artist_id"),
            "confidence": 0.9,
            "raw_metadata_json": _json_param(row.get("raw_metadata_json") or {}),
            "provenance": _json_param(
                {
                    "phase": PHASE,
                    "adapter": "gallery-dl",
                    "metadata_only": True,
                    "original_downloaded": False,
                    "raw_provider_json_private_ref": row.get("raw_provider_json_private_ref"),
                }
            ),
        },
    ).scalar_one()
    return int(result), action


def _upsert_tag_observation(
    conn: Connection,
    source_metadata_record_id: int,
    provider_record_key: str,
    tag: str,
    *,
    order_index: int,
    tag_kind: str = "pixiv_tag",
) -> tuple[int, str]:
    normalized = normalize_text(tag)
    key = canonical_key(normalized)
    observation_key = f"{provider_record_key}:tag:{key}:{order_index}"
    existing = conn.execute(
        text(
            """
            SELECT id
            FROM blombooru_source_tag_observations
            WHERE source_metadata_record_id = :source_metadata_record_id
              AND observation_key = :observation_key
            """
        ),
        {"source_metadata_record_id": source_metadata_record_id, "observation_key": observation_key},
    ).scalar()
    action = "updated" if existing else "inserted"
    result = conn.execute(
        text(
            """
            INSERT INTO blombooru_source_tag_observations (
                source_metadata_record_id, provider, observation_key, raw_tag,
                normalized_tag, canonical_tag_key, source_tag_kind, source_category_raw,
                language_hint, confidence, order_index, status, updated_at
            )
            VALUES (
                :source_metadata_record_id, 'pixiv', :observation_key, :raw_tag,
                :normalized_tag, :canonical_tag_key, :source_tag_kind, NULL,
                :language_hint, 0.75, :order_index, 'observed', now()
            )
            ON CONFLICT (source_metadata_record_id, observation_key) DO UPDATE SET
                raw_tag = EXCLUDED.raw_tag,
                normalized_tag = EXCLUDED.normalized_tag,
                canonical_tag_key = EXCLUDED.canonical_tag_key,
                source_tag_kind = EXCLUDED.source_tag_kind,
                language_hint = EXCLUDED.language_hint,
                confidence = EXCLUDED.confidence,
                order_index = EXCLUDED.order_index,
                status = EXCLUDED.status,
                updated_at = now()
            RETURNING id
            """
        ),
        {
            "source_metadata_record_id": source_metadata_record_id,
            "observation_key": observation_key,
            "raw_tag": normalized,
            "normalized_tag": normalized,
            "canonical_tag_key": key,
            "source_tag_kind": tag_kind,
            "language_hint": language_script_hint(normalized)[0],
            "order_index": order_index,
        },
    ).scalar_one()
    return int(result), action


def _upsert_name_observation(
    conn: Connection,
    source_metadata_record_id: int,
    provider_record_key: str,
    media_id: int,
    work_id: str,
    page_index: int,
    raw_name: str,
    *,
    role: str,
    source_field: str,
    confidence: float,
) -> tuple[int, str]:
    normalized = normalize_text(raw_name)
    key = canonical_key(normalized)
    observation_key = f"{provider_record_key}:name:{source_field}:{role}:{key}"
    existing = conn.execute(
        text(
            """
            SELECT id
            FROM blombooru_source_name_observations
            WHERE source_metadata_record_id = :source_metadata_record_id
              AND observation_key = :observation_key
            """
        ),
        {"source_metadata_record_id": source_metadata_record_id, "observation_key": observation_key},
    ).scalar()
    action = "updated" if existing else "inserted"
    language, script = language_script_hint(normalized)
    result = conn.execute(
        text(
            """
            INSERT INTO blombooru_source_name_observations (
                source_metadata_record_id, provider, observation_key, media_id,
                source_work_id, source_page_index, raw_name, normalized_name,
                canonical_name_key, name_role, source_field, language_hint,
                script_hint, confidence, provenance, requires_review, status, updated_at
            )
            VALUES (
                :source_metadata_record_id, 'pixiv', :observation_key, :media_id,
                :source_work_id, :source_page_index, :raw_name, :normalized_name,
                :canonical_name_key, :name_role, :source_field, :language_hint,
                :script_hint, :confidence, CAST(:provenance AS jsonb), true, 'observed', now()
            )
            ON CONFLICT (source_metadata_record_id, observation_key) DO UPDATE SET
                media_id = EXCLUDED.media_id,
                source_work_id = EXCLUDED.source_work_id,
                source_page_index = EXCLUDED.source_page_index,
                raw_name = EXCLUDED.raw_name,
                normalized_name = EXCLUDED.normalized_name,
                canonical_name_key = EXCLUDED.canonical_name_key,
                name_role = EXCLUDED.name_role,
                source_field = EXCLUDED.source_field,
                language_hint = EXCLUDED.language_hint,
                script_hint = EXCLUDED.script_hint,
                confidence = EXCLUDED.confidence,
                provenance = EXCLUDED.provenance,
                requires_review = EXCLUDED.requires_review,
                status = EXCLUDED.status,
                updated_at = now()
            RETURNING id
            """
        ),
        {
            "source_metadata_record_id": source_metadata_record_id,
            "observation_key": observation_key,
            "media_id": media_id,
            "source_work_id": work_id,
            "source_page_index": page_index,
            "raw_name": normalized,
            "normalized_name": normalized,
            "canonical_name_key": key,
            "name_role": role,
            "source_field": source_field,
            "language_hint": language,
            "script_hint": script,
            "confidence": confidence,
            "provenance": _json_param({"phase": PHASE, "source_field": source_field}),
        },
    ).scalar_one()
    return int(result), action


def _upsert_metadata_evidence(
    conn: Connection,
    source_metadata_record_id: int,
    evidence_key: str,
    observation_type: str,
    observation_id: int | None,
    *,
    evidence_kind: str,
    evidence_strength: str,
) -> tuple[int, str]:
    existing = conn.execute(
        text(
            """
            SELECT id
            FROM blombooru_source_metadata_evidence
            WHERE source_metadata_record_id = :source_metadata_record_id
              AND evidence_key = :evidence_key
            """
        ),
        {"source_metadata_record_id": source_metadata_record_id, "evidence_key": evidence_key},
    ).scalar()
    action = "updated" if existing else "inserted"
    result = conn.execute(
        text(
            """
            INSERT INTO blombooru_source_metadata_evidence (
                source_metadata_record_id, evidence_key, observation_type,
                observation_id, evidence_kind, evidence_strength, provenance,
                status, updated_at
            )
            VALUES (
                :source_metadata_record_id, :evidence_key, :observation_type,
                :observation_id, :evidence_kind, :evidence_strength,
                CAST(:provenance AS jsonb), 'staged', now()
            )
            ON CONFLICT (source_metadata_record_id, evidence_key) DO UPDATE SET
                observation_type = EXCLUDED.observation_type,
                observation_id = EXCLUDED.observation_id,
                evidence_kind = EXCLUDED.evidence_kind,
                evidence_strength = EXCLUDED.evidence_strength,
                provenance = EXCLUDED.provenance,
                status = EXCLUDED.status,
                updated_at = now()
            RETURNING id
            """
        ),
        {
            "source_metadata_record_id": source_metadata_record_id,
            "evidence_key": evidence_key,
            "observation_type": observation_type,
            "observation_id": observation_id,
            "evidence_kind": evidence_kind,
            "evidence_strength": evidence_strength,
            "provenance": _json_param({"phase": PHASE}),
        },
    ).scalar_one()
    return int(result), action


def _upsert_searchable_assertion(
    conn: Connection,
    source_metadata_record_id: int,
    source_name_observation_id: int | None,
    provider_record_key: str,
    raw_input: str,
    *,
    role: str,
    confidence: str,
) -> tuple[int, str]:
    normalized = normalize_text(raw_input)
    key = canonical_key(normalized)
    assertion_key = f"{provider_record_key}:assertion:{role}:{key}"
    existing_row = conn.execute(
        text(
            """
            SELECT id, status, requires_review
            FROM blombooru_source_searchable_name_assertions
            WHERE assertion_key = :assertion_key
            """
        ),
        {"assertion_key": assertion_key},
    ).mappings().first()
    action = "updated" if existing_row else "inserted"
    result = conn.execute(
        text(
            """
            INSERT INTO blombooru_source_searchable_name_assertions (
                provider, source_metadata_record_id, source_name_observation_id,
                assertion_key, raw_input, normalized_input, canonical_name_key,
                asserted_name, asserted_role, status, confidence, confidence_score,
                evidence_sources_json, structured_output_schema_version,
                provenance_summary, requires_review, updated_at
            )
            VALUES (
                'pixiv', :source_metadata_record_id, :source_name_observation_id,
                :assertion_key, :raw_input, :normalized_input, :canonical_name_key,
                :asserted_name, :asserted_role, :assertion_status, :confidence,
                :confidence_score, CAST(:evidence_sources_json AS jsonb),
                :schema_version,
                CAST(:provenance_summary AS jsonb), true, now()
            )
            ON CONFLICT (assertion_key) DO UPDATE SET
                source_metadata_record_id = EXCLUDED.source_metadata_record_id,
                source_name_observation_id = EXCLUDED.source_name_observation_id,
                raw_input = EXCLUDED.raw_input,
                normalized_input = EXCLUDED.normalized_input,
                canonical_name_key = EXCLUDED.canonical_name_key,
                asserted_name = EXCLUDED.asserted_name,
                asserted_role = EXCLUDED.asserted_role,
                status = CASE
                    WHEN blombooru_source_searchable_name_assertions.requires_review IS FALSE
                         AND blombooru_source_searchable_name_assertions.status IN ('searchable_active', 'accepted', 'active')
                    THEN blombooru_source_searchable_name_assertions.status
                    ELSE EXCLUDED.status
                END,
                confidence = EXCLUDED.confidence,
                confidence_score = EXCLUDED.confidence_score,
                evidence_sources_json = EXCLUDED.evidence_sources_json,
                provenance_summary = EXCLUDED.provenance_summary,
                requires_review = CASE
                    WHEN blombooru_source_searchable_name_assertions.requires_review IS FALSE
                         AND blombooru_source_searchable_name_assertions.status IN ('searchable_active', 'accepted', 'active')
                    THEN blombooru_source_searchable_name_assertions.requires_review
                    ELSE EXCLUDED.requires_review
                END,
                updated_at = now()
            RETURNING id
            """
        ),
        {
            "source_metadata_record_id": source_metadata_record_id,
            "source_name_observation_id": source_name_observation_id,
            "assertion_key": assertion_key,
            "raw_input": normalized,
            "normalized_input": normalized,
            "canonical_name_key": key,
            "asserted_name": normalized,
            "asserted_role": role,
            "assertion_status": PX1_SEARCHABLE_ASSERTION_STATUS,
            "confidence": confidence,
            "confidence_score": 0.85 if confidence == "medium" else 0.75,
            "evidence_sources_json": _json_param({"phase": PHASE, "source": "gallery-dl-pixiv"}),
            "provenance_summary": _json_param({"phase": PHASE, "provider_record_key": provider_record_key}),
            "schema_version": PX1_SEARCHABLE_ASSERTION_SCHEMA_VERSION,
        },
    ).scalar_one()
    return int(result), action


def persist_source_metadata_successes(
    conn: Connection,
    successes: Sequence[Mapping[str, Any]],
    *,
    run_id: str,
) -> list[dict[str, Any]]:
    write_rows: list[dict[str, Any]] = []
    for row in successes:
        source_metadata_id, metadata_action = _upsert_source_metadata(conn, row, run_id)
        provider_record_key = f"gallery-dl-real-pixiv:metadata:{row['work_id']}:p{row['page_index']}:m{row['media_id']}"
        counts = Counter({"SourceMetadataRecord_" + metadata_action: 1})
        tag_ids: list[int] = []
        for index, tag in enumerate([*row.get("tags", []), *row.get("translated_tags", [])]):
            if not normalize_text(tag):
                continue
            tag_id, action = _upsert_tag_observation(
                conn,
                source_metadata_id,
                provider_record_key,
                tag,
                order_index=index,
                tag_kind="pixiv_translated_tag" if index >= len(row.get("tags", [])) else "pixiv_tag",
            )
            tag_ids.append(tag_id)
            counts["SourceTagObservation_" + action] += 1
            evidence_id, evidence_action = _upsert_metadata_evidence(
                conn,
                source_metadata_id,
                f"{provider_record_key}:tag-evidence:{tag_id}",
                "source_tag_observation",
                tag_id,
                evidence_kind="provider_structured_tag",
                evidence_strength="medium",
            )
            counts["SourceMetadataEvidence_" + evidence_action] += 1
        name_rows = [
            ("artist", row.get("artist_name"), "pixiv_user_metadata", 0.94),
            ("work_title", row.get("title"), "pixiv_title", 0.72),
        ]
        name_ids: list[int] = []
        for role, raw_name, source_field, confidence in name_rows:
            if not normalize_text(raw_name):
                continue
            name_id, action = _upsert_name_observation(
                conn,
                source_metadata_id,
                provider_record_key,
                int(row["media_id"]),
                str(row["work_id"]),
                int(row["page_index"]),
                str(raw_name),
                role=role,
                source_field=source_field,
                confidence=confidence,
            )
            name_ids.append(name_id)
            counts["SourceNameObservation_" + action] += 1
            _, assertion_action = _upsert_searchable_assertion(
                conn,
                source_metadata_id,
                name_id,
                provider_record_key,
                str(raw_name),
                role=role,
                confidence="medium",
            )
            counts["SourceSearchableNameAssertion_" + assertion_action] += 1
        write_rows.append(
            {
                "media_id": row["media_id"],
                "private_media_ref": stable_private_id(row["media_id"], "media"),
                "source_metadata_record_id": source_metadata_id,
                "provider_record_key_private": provider_record_key,
                "tag_observation_count": len(tag_ids),
                "name_observation_count": len(name_ids),
                "source_assertion_count": counts.get("SourceSearchableNameAssertion_inserted", 0)
                + counts.get("SourceSearchableNameAssertion_updated", 0),
                "write_counts": dict(counts),
                "allowed_tables_only": True,
            }
        )
    return write_rows


def redact_public_text(value: str) -> str:
    value = SECRET_RE.sub("[secret-redacted]", value)
    value = LOCAL_PATH_RE.sub("[path-redacted]", value)
    value = MEDIA_FILENAME_RE.sub("[filename-redacted]", value)
    return value


def scan_public_text(value: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for name, pattern in (
        ("secret_like", SECRET_RE),
        ("local_path_like", LOCAL_PATH_RE),
        ("media_filename_like", MEDIA_FILENAME_RE),
    ):
        match = pattern.search(value)
        if match:
            findings.append({"type": name, "match": match.group(0)[:120]})
    return findings


def scan_public_artifacts(paths: Sequence[Path]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    for path in paths:
        label = path.name
        if not path.exists():
            findings.append({"path": label, "type": "missing_public_artifact"})
            continue
        for finding in scan_public_text(path.read_text(encoding="utf-8", errors="replace")):
            findings.append({"path": label, **finding})
    return {"checked_paths": [path.name for path in paths], "findings": findings, "passed": not findings}


def validate_summary_schema(summary: Mapping[str, Any]) -> dict[str, Any]:
    missing = sorted(SUMMARY_REQUIRED_FIELDS - set(summary))
    return {"passed": not missing, "missing_fields": missing, "required_fields": sorted(SUMMARY_REQUIRED_FIELDS)}


def build_execution_policy(*, inventory_only: bool, execute_metadata: bool) -> dict[str, Any]:
    return {
        "inventory_only": inventory_only,
        "execute_metadata": execute_metadata,
        "dedup_write_allowed": False,
        "duplicate_deletion_option_present": False,
        "db_write_allowed": bool(execute_metadata and not inventory_only),
        "allowed_write_tables": sorted(ALLOWED_SOURCE_WRITE_TABLES),
    }


def searchable_assertion_write_policy() -> dict[str, Any]:
    return {
        "new_px1_assertion_status": PX1_SEARCHABLE_ASSERTION_STATUS,
        "new_px1_requires_review": True,
        "new_px1_searchable_active": False,
        "preserve_existing_reviewed_active_or_accepted": True,
        "schema_version": PX1_SEARCHABLE_ASSERTION_SCHEMA_VERSION,
    }


def public_command_label(argv: Sequence[str]) -> str:
    parts = [Path(sys.executable).name]
    for item in argv:
        value = str(item).replace("\\", "/")
        root_text = str(ROOT).replace("\\", "/")
        if value.startswith(root_text):
            value = Path(value).name
        parts.append(value)
    return " ".join(parts)


def recommended_next_phase(
    *,
    metadata_success_count: int,
    metadata_failure_count: int,
    provider_stop_reason: str | None,
    pixiv_backlog_remaining: int,
    duplicate_auto_delete_groups: int,
    duplicate_risk_groups: int,
) -> str:
    if provider_stop_reason in {"repeated_auth_or_config_failures", "repeated_rate_limit_failures"}:
        return "provider/auth hardening before SCV2-R1"
    if metadata_success_count > 0 and metadata_failure_count == 0 and pixiv_backlog_remaining > 0:
        return "PX1-B"
    if duplicate_auto_delete_groups > 0 and duplicate_risk_groups == 0:
        return "Phase 4.5-DEDUP1: Exact Duplicate Cleanup Execution"
    if duplicate_risk_groups > 0:
        return "DEDUP merge/retention policy design before destructive cleanup"
    if metadata_success_count > 0:
        return "SCV2-R1"
    return "PX1 precondition resolution"


def public_report_markdown(summary: Mapping[str, Any]) -> str:
    baseline = summary["baseline"]
    inv = summary["pixiv_like_inventory"]
    dup = summary["duplicate_cleanup_plan_summary"]
    selection = summary["metadata_selection"]
    provider = summary["provider_preflight"]
    results = summary["metadata_extraction_results"]
    writes = summary["source_layer_write_results"]
    mutation = summary["mutation_proof"]
    lines = [
        f"# {PHASE} {PHASE_TITLE}",
        "",
        "## Summary",
        "",
        f"- Status: `{summary.get('status')}`.",
        f"- Current total media: `{baseline.get('total_media')}`.",
        f"- Pixiv-like candidates: `{inv.get('pixiv_like_media_candidates')}`.",
        f"- Metadata extraction selected/success/failure: `{selection.get('selected_count')}` / `{results.get('success_count')}` / `{results.get('failure_count')}`.",
        f"- Exact duplicate groups: `{dup.get('exact_duplicate_groups')}`; would-delete if later approved: `{dup.get('would_delete_count_if_later_approved')}`.",
        f"- Provider execution policy: `{selection.get('provider_execution_policy')}`.",
        "",
        "## Scope and non-goals",
        "",
        "- PX1 only inventories DB-derived Pixiv-like media, runs exact-hash duplicate dry-run, and may run bounded metadata-only Pixiv/gallery-dl requests.",
        "- It does not import media, delete duplicates, mutate source/iCloud storage, run AI/classification/localization/LLM, run SourceConcept resolver, create Entity truth, or write media_tags.",
        "",
        "## Current post-E1 baseline",
        "",
        f"- Total media: `{baseline.get('total_media')}`.",
        f"- Eligible media: `{baseline.get('eligible_media')}`.",
        f"- Eligible AI tag coverage: `{baseline.get('eligible_media_with_ai_tag_provenance')}` / `{baseline.get('eligible_media')}` (`{baseline.get('eligible_ai_tag_provenance_pct')}%`).",
        "",
        "## Current Pixiv-like inventory",
        "",
        f"- Pixiv-like media candidates: `{inv.get('pixiv_like_media_candidates')}`.",
        f"- With existing source metadata: `{inv.get('pixiv_like_candidates_with_existing_source_metadata')}`.",
        f"- Without source metadata: `{inv.get('pixiv_like_candidates_without_source_metadata')}`.",
        f"- Distinct Pixiv work IDs: `{inv.get('distinct_pixiv_work_ids')}`.",
        f"- Duplicate Pixiv work/page candidates: `{inv.get('duplicate_pixiv_work_page_candidates')}`.",
        f"- Invalid or ambiguous Pixiv ID candidates: `{inv.get('invalid_or_ambiguous_pixiv_id_candidates')}`.",
        f"- Eligible for metadata extraction before limit: `{inv.get('candidates_eligible_for_metadata_extraction')}`.",
        f"- Anime provider execution eligible: `{inv.get('provider_execution_eligible_anime_candidates')}`.",
        f"- Unknown excluded from provider execution: `{inv.get('provider_execution_excluded_unknown')}`.",
        f"- Non-anime excluded from provider execution: `{inv.get('provider_execution_excluded_non_anime')}`.",
        f"- Already has source metadata excluded: `{inv.get('provider_execution_excluded_already_has_source_metadata')}`.",
        f"- Exclusion reasons: `{json.dumps(inv.get('exclusion_reason_counts'), sort_keys=True)}`.",
        f"- Provider execution exclusion reasons: `{json.dumps(inv.get('provider_execution_exclusion_reason_counts'), sort_keys=True)}`.",
        "",
        "## Exact duplicate dry-run summary",
        "",
        f"- Exact duplicate groups: `{dup.get('exact_duplicate_groups')}`.",
        f"- Duplicate media involved: `{dup.get('total_duplicate_media_involved')}`.",
        f"- Would-delete count if later approved: `{dup.get('would_delete_count_if_later_approved')}`.",
        f"- Estimated reclaim bytes if safely computable: `{dup.get('estimated_storage_reclaim_bytes_if_safely_computable')}`.",
        "",
        "## Duplicate retention policy result",
        "",
        f"- Pixiv-retained groups: `{dup.get('groups_with_pixiv_like_retention')}`.",
        f"- All-non-Pixiv groups: `{dup.get('all_non_pixiv_groups')}`.",
        f"- Ambiguous/conflicting Pixiv groups: `{dup.get('ambiguous_conflicting_pixiv_groups')}`.",
        f"- Attached-data risk groups: `{dup.get('groups_blocked_by_attached_data_risk')}`.",
        "- Duplicate deletion may be proposed later only as a separate destructive phase.",
        "",
        "## Metadata extraction candidate selection",
        "",
        f"- Selected count: `{selection.get('selected_count')}`.",
        f"- Eligible before limit: `{selection.get('eligible_before_limit')}`.",
        f"- Requested limit: `{selection.get('requested_limit')}`.",
        f"- Excluded reason counts: `{json.dumps(selection.get('excluded_reason_counts'), sort_keys=True)}`.",
        f"- Unknown excluded from provider execution: `{selection.get('unknown_excluded_from_provider_execution')}`.",
        f"- Non-anime excluded from provider execution: `{selection.get('non_anime_excluded_from_provider_execution')}`.",
        "",
        "## Provider/auth/cache/rate-limit preflight",
        "",
        f"- gallery-dl available: `{provider.get('gallery_dl_available')}`.",
        f"- Entry mode: `{provider.get('entrypoint', {}).get('mode')}`.",
        f"- Original download policy: `{provider.get('original_download_policy')}`.",
        f"- Provider cache: `{json.dumps(provider.get('provider_cache'), sort_keys=True)}`.",
        "",
        "## Metadata extraction execution results",
        "",
        f"- Execution mode: `{results.get('mode')}`.",
        f"- Attempted: `{results.get('attempted_count')}`.",
        f"- Success: `{results.get('success_count')}`.",
        f"- Failure: `{results.get('failure_count')}`.",
        f"- Failure reason counts: `{json.dumps(results.get('failure_reason_counts'), sort_keys=True)}`.",
        f"- Provider output diagnosis: `{json.dumps(results.get('provider_output_diagnosis'), sort_keys=True)}`.",
        f"- Stop reason: `{results.get('stop_reason')}`.",
        "",
        "## Source-layer write results",
        "",
        f"- Writes applied: `{writes.get('writes_applied')}`.",
        f"- Source metadata records affected: `{writes.get('source_metadata_records_affected')}`.",
        f"- Tag observations affected: `{writes.get('tag_observations_affected')}`.",
        f"- Name observations affected: `{writes.get('name_observations_affected')}`.",
        f"- Assertions affected: `{writes.get('source_assertions_affected')}`.",
        f"- Searchable assertion status policy: `{json.dumps(summary.get('assertion_status_policy'), sort_keys=True)}`.",
        "",
        "## Failure budget and stop conditions",
        "",
        f"`{json.dumps(summary['failure_budget'], ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Mutation proof",
        "",
        f"- Passed: `{mutation.get('passed')}`.",
        f"- Expected changed tables: `{json.dumps(mutation.get('expected_changed_table_names'), sort_keys=True)}`.",
        f"- Forbidden changed tables: `{json.dumps(mutation.get('forbidden_changed_table_names'), sort_keys=True)}`.",
        f"- Unexpected changed tables: `{json.dumps(mutation.get('unexpected_changed_table_names'), sort_keys=True)}`.",
        "",
        "## Public/private artifact boundary",
        "",
        "- Public report contains aggregate counts only; exact media IDs, hashes, Pixiv IDs, filenames, local paths, raw provider JSON, cookies, and tokens remain private.",
        f"- Private artifact root label: `{summary['private_artifacts'].get('private_artifact_root_label')}`.",
        "",
        "## Decision matrix",
        "",
        f"`{json.dumps(summary['decision_matrix'], ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Whether PX1 target was met",
        "",
        f"- PX1 target met: `{summary['decision_matrix'].get('px1_target_met')}`.",
        "",
        "## Whether duplicate deletion may be proposed as a later destructive phase",
        "",
        f"- May propose later destructive DEDUP1: `{summary['decision_matrix'].get('dedup1_may_be_proposed')}`.",
        "",
        "## Whether SCV2-R1 may start next",
        "",
        f"- SCV2-R1 may start next: `{summary['decision_matrix'].get('scv2_r1_may_start')}`.",
        "",
        "## Deferred work",
        "",
        "- Duplicate deletion execution is deferred.",
        "- PX1-B is deferred unless separately approved.",
        "- SCV2-R1 must not start inside this PR.",
        "",
        "## Validation",
        "",
        f"`{json.dumps(summary['validation'], ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Safety confirmation",
        "",
        "- No push main and no merge.",
        "- No duplicate deletion, media deletion, DB row deletion, reset, cleanup, drop, or truncate.",
        "- No media import, classification, AI tagging, localization, LLM, SourceConcept resolver, Entity bridge, media_tags mutation, source/iCloud mutation, or original image download.",
        "",
        "## Artifact lifecycle",
        "",
        f"`{json.dumps(summary['artifact_lifecycle'], ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Engineering judgment / operator notes",
        "",
        "PX1 keeps the provider run bounded and source-layer-only. The dedup plan is useful for avoiding wasted metadata extraction, but deletion remains destructive and must be split into a separately approved phase. If provider/auth fails, the correct next step is provider/auth hardening rather than forcing SCV2-R1 with weak source metadata coverage.",
        "",
    ]
    return "\n".join(lines)


def write_public_outputs(summary: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    temp_dir = output_dir / "_public_report_staging"
    temp_dir.mkdir(parents=True, exist_ok=True)
    PUBLIC_REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    temp_md = temp_dir / PUBLIC_REPORT_MD.name
    temp_json = temp_dir / PUBLIC_REPORT_JSON.name
    temp_summary = dict(summary)
    temp_summary["public_redaction"] = {"passed": True, "findings": [], "checked_paths": []}
    write_text(temp_md, public_report_markdown(temp_summary))
    write_json(temp_json, temp_summary)
    redaction = scan_public_artifacts([temp_md, temp_json])
    redaction["checked_paths"] = [PUBLIC_REPORT_MD.name, PUBLIC_REPORT_JSON.name]
    if not redaction["passed"]:
        write_text(output_dir / "public-redaction-check.txt", json.dumps(redaction, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        raise PX1BlockedError("public_redaction_failed")
    summary["public_redaction"] = redaction
    write_text(temp_md, public_report_markdown(summary))
    write_json(temp_json, summary)
    os.replace(temp_md, PUBLIC_REPORT_MD)
    os.replace(temp_json, PUBLIC_REPORT_JSON)
    write_text(output_dir / "public-redaction-check.txt", json.dumps(redaction, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    try:
        temp_dir.rmdir()
    except OSError:
        pass
    return redaction


def build_public_summary(
    *,
    status: str,
    baseline: Mapping[str, Any],
    db_identity_before: Mapping[str, Any],
    db_identity_after: Mapping[str, Any],
    pixiv_inventory: Mapping[str, Any],
    duplicate_summary: Mapping[str, Any],
    metadata_selection: Mapping[str, Any],
    provider_preflight: Mapping[str, Any],
    metadata_results: Mapping[str, Any],
    write_results: Mapping[str, Any],
    mutation_delta: Mapping[str, Any],
    failure_budget: Mapping[str, Any],
    provider_cache_summary: Mapping[str, Any],
    validation: Mapping[str, Any],
) -> dict[str, Any]:
    def public_identity(value: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(value)
        executable = str(result.get("python_executable") or "")
        if executable:
            result["python_executable"] = Path(executable).name
            result["python_executable_path_recorded_private_only"] = True
        return result

    provider_cache_public = dict(provider_cache_summary)
    if "raw_json_cache_dir_private" in provider_cache_public:
        provider_cache_public["raw_json_cache_dir_private"] = True
        provider_cache_public["raw_json_cache_dir_public_label"] = f".local_manifests/{PHASE_SLUG}/provider-cache/raw-gallery-dl-json"

    pixiv_remaining = int(pixiv_inventory.get("pixiv_like_candidates_without_source_metadata") or 0) - int(metadata_results.get("success_count") or 0)
    next_phase = recommended_next_phase(
        metadata_success_count=int(metadata_results.get("success_count") or 0),
        metadata_failure_count=int(metadata_results.get("failure_count") or 0),
        provider_stop_reason=metadata_results.get("stop_reason"),
        pixiv_backlog_remaining=max(pixiv_remaining, 0),
        duplicate_auto_delete_groups=int(duplicate_summary.get("auto_delete_candidate_groups_if_later_approved") or 0),
        duplicate_risk_groups=int(duplicate_summary.get("groups_blocked_by_attached_data_risk") or 0),
    )
    decision_matrix = {
        "px1_target_met": bool(metadata_results.get("success_count")) and mutation_delta.get("passed") is True,
        "metadata_extraction_succeeded_for_bounded_batch": int(metadata_results.get("success_count") or 0) > 0,
        "provider_auth_or_rate_limit_blocked": metadata_results.get("stop_reason")
        in {"repeated_auth_or_config_failures", "repeated_rate_limit_failures"},
        "dedup1_may_be_proposed": int(duplicate_summary.get("auto_delete_candidate_groups_if_later_approved") or 0) > 0,
        "scv2_r1_may_start": int(metadata_results.get("success_count") or 0) > 0
        and mutation_delta.get("passed") is True
        and metadata_results.get("stop_reason") not in {"repeated_auth_or_config_failures", "repeated_rate_limit_failures"},
        "recommended_next_phase": next_phase,
    }
    summary = {
        "phase": PHASE,
        "title": PHASE_TITLE,
        "status": status,
        "branch": BRANCH,
        "generated_at": utc_now(),
        "db_identity_before": public_identity(db_identity_before),
        "db_identity_after": public_identity(db_identity_after),
        "baseline": dict(baseline),
        "pixiv_like_inventory": dict(pixiv_inventory),
        "duplicate_dry_run": {
            "read_only": True,
            "exact_hash_only": True,
            "perceptual_or_similarity_detection": False,
            "deletion_executed": False,
        },
        "duplicate_cleanup_plan_summary": dict(duplicate_summary),
        "metadata_selection": dict(metadata_selection),
        "provider_preflight": {**dict(provider_preflight), "provider_cache": provider_cache_public},
        "metadata_extraction_results": dict(metadata_results),
        "source_layer_write_results": dict(write_results),
        "assertion_status_policy": searchable_assertion_write_policy(),
        "mutation_proof": dict(mutation_delta),
        "failure_budget": dict(failure_budget),
        "public_redaction": {"passed": None, "findings": [], "checked_paths": []},
        "decision_matrix": decision_matrix,
        "recommended_next_phase": next_phase,
        "validation": dict(validation),
        "safety": {
            "no_push_main": True,
            "no_merge": True,
            "no_duplicate_deletion": True,
            "no_media_import": True,
            "no_classification_ai_localization_llm": True,
            "no_source_concept_resolver_or_entity_bridge": True,
            "no_media_tags_mutation": True,
            "no_source_icloud_mutation": True,
            "no_original_image_download": True,
            "browser_validation": "not_run_no_ui_runtime_target",
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
        raise PX1BlockedError("summary_schema_missing:" + ",".join(schema["missing_fields"]))
    return summary


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    assert_branch()
    if not args.inventory_only and not args.execute_metadata:
        raise PX1BlockedError("PX1 requires --inventory-only or --execute-metadata.")
    if args.execute_metadata and args.confirm_metadata_execution != CONFIRM_PHRASE:
        raise PX1BlockedError(f"--execute-metadata requires --confirm-metadata-execution {CONFIRM_PHRASE}")
    if not args.read_only_dedup:
        raise PX1BlockedError("PX1 requires --read-only-dedup; duplicate deletion is not available.")

    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    engine, db_identity_source = build_database_engine()
    request_rows: list[dict[str, Any]] = []
    success_rows: list[dict[str, Any]] = []
    failure_rows: list[dict[str, Any]] = []
    write_rows: list[dict[str, Any]] = []
    provider_cache_summary: dict[str, Any] = {
        "filesystem_provider_cache_used": True,
        "db_provider_cache_used": False,
        "raw_json_cache_dir_private": str(output_dir / "provider-cache" / "raw-gallery-dl-json"),
        "request_count": 0,
        "success_count": 0,
        "failure_count": 0,
        "failure_reason_counts": {},
        "cache_hit_count": 0,
        "cache_miss_count": 0,
        "provider_called_count": 0,
        "cache_parse_failure_count": 0,
        "raw_failure_artifact_count": 0,
        "provider_output_diagnosis": provider_output_diagnosis_summary([]),
        "original_downloaded": False,
    }
    entrypoint = GalleryDlEntrypoint(False, "not_probed_inventory_only")
    provider_budget = ProviderFailureBudget(
        max_auth_failures=args.max_auth_failures,
        max_rate_limit_failures=args.max_rate_limit_failures,
        max_total_failures=args.max_provider_failures,
        max_failure_rate=args.max_provider_failure_rate,
        max_consecutive_failures=args.max_consecutive_provider_failures,
    )
    try:
        with engine.connect() as conn:
            db_identity_before = public_db_identity(db_identity_source, conn)
            baseline = p0.audit_current_media_baseline(conn)
            mutation_before = build_table_state(conn)
            candidates = load_media_candidates(conn)
            duplicate_plans, duplicate_summary = build_duplicate_dry_run(conn, candidates)
            pixiv_inventory = build_pixiv_inventory(baseline, candidates, duplicate_summary, duplicate_plans)
            selected, selection_summary = select_metadata_targets(candidates, duplicate_plans, limit=args.metadata_limit)

        write_json(output_dir / "db-identity-before.json", db_identity_before)
        write_json(output_dir / "pixiv-like-inventory.json", pixiv_inventory)
        write_jsonl(output_dir / "pixiv-like-candidates.jsonl", [candidate_private_row(item) for item in candidates if item.pixiv_like])
        write_jsonl(output_dir / "duplicate-groups-dry-run.jsonl", duplicate_plans)
        write_jsonl(output_dir / "duplicate-cleanup-plan.jsonl", duplicate_plans)
        write_json(output_dir / "duplicate-cleanup-summary.json", duplicate_summary)
        write_json(output_dir / "mutation-proof-before.json", mutation_before)

        provider_preflight = {
            "gallery_dl_available": False,
            "entrypoint": entrypoint.public_dict(),
            "original_download_policy": "forbidden; command uses --dump-json --no-download",
            "network_attempted": False,
            "auth_status": "not_checked_inventory_only",
            "rate_limit_policy": "serial requests, bounded failure budget",
        }
        metadata_results = {
            "mode": "inventory_only" if args.inventory_only else "execute_metadata",
            "selected_count": selection_summary["selected_count"],
            "attempted_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "failure_reason_counts": {},
            "provider_output_diagnosis": provider_output_diagnosis_summary([]),
            "stop_reason": None,
            "original_downloaded": False,
        }
        write_results = {
            "writes_applied": False,
            "source_metadata_records_affected": 0,
            "tag_observations_affected": 0,
            "name_observations_affected": 0,
            "source_assertions_affected": 0,
            "write_ledger_count": 0,
            "allowed_tables_only": True,
        }
        status = "inventory_only_completed"

        if args.execute_metadata and not args.inventory_only:
            entrypoint = probe_gallery_dl_entrypoint(args.gallery_dl_command or None)
            provider_preflight.update(
                {
                    "gallery_dl_available": entrypoint.available,
                    "entrypoint": entrypoint.public_dict(),
                    "auth_status": "deferred_to_first_metadata_request",
                }
            )
            if entrypoint.available:
                request_rows, success_rows, failure_rows, provider_cache_summary = execute_metadata_requests(
                    selected,
                    entrypoint,
                    output_dir=output_dir,
                    timeout=args.provider_timeout,
                    sleep_request_seconds=args.sleep_request_seconds,
                    failure_budget=provider_budget,
                )
                provider_preflight["network_attempted"] = bool(request_rows)
                provider_preflight["auth_status"] = "ok_or_not_required" if success_rows else (
                    "auth_or_config_failed" if any(row.get("failure_reason") == "auth_or_config_failure" for row in failure_rows) else "unknown_no_success"
                )
                metadata_results.update(
                    {
                        "attempted_count": len(request_rows),
                        "success_count": len(success_rows),
                        "failure_count": len(failure_rows),
                        "failure_reason_counts": dict(provider_cache_summary.get("failure_reason_counts") or {}),
                        "provider_output_diagnosis": dict(provider_cache_summary.get("provider_output_diagnosis") or {}),
                        "stop_reason": provider_budget.stop_reason,
                        "original_downloaded": False,
                    }
                )
                if success_rows:
                    with engine.begin() as conn:
                        write_rows = persist_source_metadata_successes(conn, success_rows, run_id=args.run_id or uuid.uuid4().hex)
                    write_counts = Counter()
                    for row in write_rows:
                        for key, value in row.get("write_counts", {}).items():
                            write_counts[key] += int(value)
                    write_results.update(
                        {
                            "writes_applied": True,
                            "source_metadata_records_affected": write_counts.get("SourceMetadataRecord_inserted", 0)
                            + write_counts.get("SourceMetadataRecord_updated", 0),
                            "tag_observations_affected": write_counts.get("SourceTagObservation_inserted", 0)
                            + write_counts.get("SourceTagObservation_updated", 0),
                            "name_observations_affected": write_counts.get("SourceNameObservation_inserted", 0)
                            + write_counts.get("SourceNameObservation_updated", 0),
                            "source_assertions_affected": write_counts.get("SourceSearchableNameAssertion_inserted", 0)
                            + write_counts.get("SourceSearchableNameAssertion_updated", 0),
                            "write_ledger_count": len(write_rows),
                            "allowed_tables_only": True,
                        }
                    )
                    status = "metadata_execution_completed"
                else:
                    status = "inventory_and_dedup_completed_provider_metadata_not_written"
            else:
                status = "inventory_and_dedup_completed_missing_gallery_dl"

        write_jsonl(output_dir / "pixiv-metadata-request-ledger.jsonl", request_rows)
        write_jsonl(output_dir / "pixiv-metadata-success-ledger.jsonl", success_rows)
        write_jsonl(output_dir / "pixiv-metadata-failure-ledger.jsonl", failure_rows)
        write_jsonl(output_dir / "source-metadata-write-ledger.jsonl", write_rows)
        write_json(output_dir / "provider-cache-summary.json", provider_cache_summary)

        with engine.connect() as conn:
            db_identity_after = public_db_identity(db_identity_source, conn)
            db_identity_after["total_media_after"] = scalar_count(conn, "SELECT COUNT(*) FROM blombooru_media")
            mutation_after = build_table_state(conn)
        write_json(output_dir / "db-identity-after.json", db_identity_after)
        write_json(output_dir / "mutation-proof-after.json", mutation_after)
        mutation_delta = classify_table_mutations(mutation_before, mutation_after)
        write_json(output_dir / "mutation-proof-delta.json", mutation_delta)
        if not mutation_delta["passed"]:
            status = "blocked_mutation_proof_failed"

        validation = {
            "operational_mode": "inventory-only" if args.inventory_only else "execute-metadata",
            "dedup_read_only": True,
            "provider_network_attempted": provider_preflight.get("network_attempted"),
            "server_started": False,
            "browser_validation": "not_run_no_ui_runtime_target",
            "commands": [public_command_label(sys.argv)],
        }
        summary = build_public_summary(
            status=status,
            baseline=baseline,
            db_identity_before=db_identity_before,
            db_identity_after=db_identity_after,
            pixiv_inventory=pixiv_inventory,
            duplicate_summary=duplicate_summary,
            metadata_selection=selection_summary,
            provider_preflight=provider_preflight,
            metadata_results=metadata_results,
            write_results=write_results,
            mutation_delta=mutation_delta,
            failure_budget=provider_budget.public_dict(),
            provider_cache_summary=provider_cache_summary,
            validation=validation,
        )
        if args.write_public_report:
            write_public_outputs(summary, output_dir)
        elif not (output_dir / "public-redaction-check.txt").exists():
            write_text(output_dir / "public-redaction-check.txt", "public report not requested\n")
        return summary
    finally:
        engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inventory-only", action="store_true")
    mode.add_argument("--execute-metadata", action="store_true")
    parser.add_argument("--metadata-limit", type=int, default=500)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write-public-report", action="store_true")
    parser.add_argument("--read-only-dedup", action="store_true")
    parser.add_argument("--confirm-metadata-execution", default="")
    parser.add_argument("--gallery-dl-command", default=os.getenv("VIOLET_GALLERY_DL_COMMAND", ""))
    parser.add_argument("--provider-timeout", type=int, default=120)
    parser.add_argument("--sleep-request-seconds", type=float, default=2.0)
    parser.add_argument("--max-auth-failures", type=int, default=3)
    parser.add_argument("--max-rate-limit-failures", type=int, default=3)
    parser.add_argument("--max-provider-failures", type=int, default=20)
    parser.add_argument("--max-provider-failure-rate", type=float, default=0.25)
    parser.add_argument("--max-consecutive-provider-failures", type=int, default=5)
    parser.add_argument("--run-id", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        summary = run_pipeline(args)
    except PX1BlockedError as exc:
        print(json.dumps({"ok": False, "phase": PHASE, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True, default=json_default))
    if summary.get("status") == "blocked_mutation_proof_failed":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
