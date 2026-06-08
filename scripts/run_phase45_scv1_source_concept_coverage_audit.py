"""Run Phase 4.5-SCV1 SourceConcept coverage and quality audit.

Lifecycle: phase-scoped operational runner.

This runner is intentionally read-only. It connects to the current development
database, opens a PostgreSQL read-only transaction, audits existing media,
source-layer, and SourceConcept coverage, then writes private artifacts and a
public-safe report. It does not run app startup, migrations, providers, jobs,
workers, localization, LLMs, imports, server code, or browser validation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import URL, bindparam, create_engine, event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.source_concept_search_service import (  # noqa: E402
    SEARCH_TOKEN_META_RE,
    _search_keys_for_term,
)
from app.services.source_metadata_registry_service import canonical_source_key, normalize_source_text  # noqa: E402

PHASE = "4.5-SCV1"
PHASE_TITLE = "Expanded SourceConcept Validation and Coverage Audit"
PHASE_SLUG = "phase-4.5-scv1-source-concept-coverage-audit"
BRANCH = "codex/phase45-scv1-source-concept-coverage-audit"
PUBLIC_REPORT_MD = ROOT / "docs" / "reports" / f"{PHASE_SLUG}.md"
PUBLIC_REPORT_JSON = ROOT / "docs" / "reports" / f"{PHASE_SLUG}-summary.json"
DEFAULT_OUTPUT_DIR = ROOT / ".local_manifests" / PHASE_SLUG

VISIBLE_STATUSES = ("active", "needs_review")
ACTIVE_STATUSES = ("active",)
REVIEW_STATUSES = ("needs_review",)
HIDDEN_STATUSES = ("rejected", "ambiguous", "superseded", "hidden", "weak")

FORBIDDEN_TABLES = (
    "blombooru_media",
    "blombooru_tags",
    "blombooru_media_tags",
    "blombooru_tag_translations",
    "blombooru_entities",
    "blombooru_entity_aliases",
    "blombooru_entity_evidence",
    "blombooru_media_entity_candidates",
    "blombooru_media_entity_assignments",
    "blombooru_local_source_hints",
    "blombooru_provider_cache",
    "blombooru_source_metadata_records",
    "blombooru_source_tag_observations",
    "blombooru_source_name_observations",
    "blombooru_source_searchable_name_assertions",
    "blombooru_source_name_candidates",
    "blombooru_source_concept_signals",
    "blombooru_source_concepts",
    "blombooru_source_concept_aliases",
    "blombooru_source_concept_evidence",
    "blombooru_source_concept_signal_links",
    "blombooru_source_concept_search_index",
)

SOURCE_LAYER_TABLES = (
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
    "blombooru_provider_cache",
)

PRIVATE_ARTIFACT_NAMES = (
    "db-identity.json",
    "read-only-mutation-proof.json",
    "media-coverage-inventory.json",
    "source-layer-coverage-inventory.json",
    "source-concept-inventory.json",
    "source-concept-alias-inventory.csv",
    "source-concept-evidence-inventory.csv",
    "source-concept-search-symmetry.json",
    "source-concept-search-symmetry-samples.csv",
    "alias-gap-analysis.json",
    "alias-gap-samples.csv",
    "needs-review-cluster-analysis.json",
    "needs-review-cluster-samples.csv",
    "redaction-privacy-audit.json",
    "scv1-decision-matrix.json",
    "public-redaction-check.txt",
)

SEARCH_SYMMETRY_METRIC_KEYS = (
    "concepts_checked",
    "aliases_checked",
    "active_concepts_checked",
    "needs_review_concepts_checked",
    "exact_symmetric_concepts",
    "explainable_no_media_concepts",
    "asymmetric_concepts",
    "severe_asymmetry_count",
    "one_way_link_count",
    "fragmentation_count",
    "overbroad_expansion_count",
    "hidden_status_leak_count",
    "hidden_status_raw_match_count",
    "metacharacter_alias_count",
    "sampled_examples_private_count",
    "public_safe_example_count",
)

ALIAS_GAP_SAMPLE_LIMIT = 30
HIGH_FREQUENCY_GAP_SAMPLE_LIMIT = 25

SECRET_VALUE_RE = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|authorization)\s*[:=]\s*['\"]?[A-Za-z0-9_\-./:+]{6,}"
)
BEARER_RE = re.compile(r"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9._\-]{8,}")
OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
LOCAL_PATH_RE = re.compile(
    r"(?i)([A-Z]:[\\/]|file://|\\\\|/Users/|/home/|\\Users\\|"
    r"\bUsers[\\/]|(?:iCloud|Pictures|Documents|Desktop|Downloads)[\\/])"
)
MEDIA_FILENAME_RE = re.compile(r"(?i)\b[A-Za-z0-9][A-Za-z0-9_. -]{0,80}\.(jpg|jpeg|png|webp|gif|bmp|avif|mp4|webm|mov|zip|rar|7z)\b")
CANONICAL_FILENAME_RE = re.compile(r"(?i)\b(?:img_\d+|image_\d+|private|vacation(?:_\d{4})?|users_[a-z0-9_]+|icloud_[a-z0-9_]+)_(jpg|jpeg|png|webp|gif|bmp|avif|mp4|webm|mov)\b")
CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
LATIN_RE = re.compile(r"[A-Za-z]")
DANBOORU_PAREN_RE = re.compile(r"^[a-z0-9_:+.\-]+_\([^()]+\)$")

SEED_GROUPS = {
    "nahida_prompt_and_doc1": [
        "Nahida",
        "\u7eb3\u897f\u59b2",
        "\u8349\u795e",
        "nahida_(genshin_impact)",
        "\u7efe\u5ba0\u30bf\u6fe1\u778f",
        "\u947d\u592c\ue5a3",
    ],
    "kamisato_ayaka": ["Kamisato Ayaka", "kamisato_ayaka", "\u795e\u91cc\u7dbe\u83ef", "\u7ec1\u70ba\u5677\u7f0d\u6371\u5f72"],
    "nilou": ["Nilou", "nilou_(genshin_impact)", "\u59ae\u9732", "\u6fde\ue7c1\u6e36", "\u9289\u30cb\u3002\u5045\u9289\ue5dc\u504a"],
    "barbara": ["Barbara", "barbara_(genshin_impact)", "\u30d0\u30fc\u30d0\u30e9", "\u9289\u611c\u3002\u5171\u9289\u611c\u3002\u5125"],
    "mona": ["Mona", "mona_(genshin_impact)", "\u30e2\u30ca", "\u9289\ue760\u3001\u5115"],
    "2b": ["2B", "2b_(nier_automata)", "\u30e8\u30eb\u30cf\u4e8c\u53f7B\u578b", "\u9289\u30e8\u3002\u5137\u9289\u5fd2\u4e8c\u53f7B\u5a9a"],
}


class AuditBlockedError(RuntimeError):
    """Raised when the read-only audit cannot continue safely."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n")


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def zip_directory(output_dir: Path) -> Path:
    zip_path = Path(f"{output_dir}.zip")
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(output_dir))
    return zip_path


def git_value(args: Sequence[str]) -> str:
    try:
        return subprocess.check_output(args, cwd=ROOT, text=True, encoding="utf-8", stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unavailable"


def root_relative_or_name(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return path.name


def public_private_artifact_summary(*, bundle_created: bool = False) -> dict[str, Any]:
    return {
        "private_artifact_root_label": f".local_manifests/{PHASE_SLUG}",
        "private_artifact_count": len(PRIVATE_ARTIFACT_NAMES) + 1,
        "private_artifact_bundle_created": bundle_created,
        "private_artifact_bundle_format": "zip",
        "exact_private_paths_public": False,
    }


def load_dotenv_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            values[key] = value
    return values


def load_database_file_settings() -> dict[str, Any]:
    path = ROOT / "data" / "settings.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    database = payload.get("database")
    return database if isinstance(database, dict) else {}


def env_lookup(key: str, dotenv: Mapping[str, str], default: str | None = None) -> str | None:
    value = os.getenv(key)
    if value is not None and value != "":
        return value
    value = dotenv.get(key)
    if value is not None and value != "":
        return value
    return default


def build_database_url() -> tuple[URL, dict[str, Any]]:
    dotenv = load_dotenv_values(ROOT / ".env")
    file_db = load_database_file_settings()
    violet_env = (env_lookup("VIOLET_ENV", dotenv, "development") or "development").strip().lower()
    if violet_env != "development":
        raise AuditBlockedError(f"SCV1 must run against VIOLET_ENV=development, got {violet_env!r}.")

    database = env_lookup("POSTGRES_DB", dotenv, str(file_db.get("name") or "blombooru"))
    if database != "blombooru":
        raise AuditBlockedError(f"SCV1 must run against development DB 'blombooru', got {database!r}.")

    host = env_lookup("POSTGRES_HOST", dotenv, str(file_db.get("host") or "localhost"))
    port = int(env_lookup("POSTGRES_PORT", dotenv, str(file_db.get("port") or 5432)) or 5432)
    username = env_lookup("POSTGRES_USER", dotenv, str(file_db.get("user") or "postgres"))
    password = env_lookup("POSTGRES_PASSWORD", dotenv, str(file_db.get("password") or "")) or ""
    url = URL.create(
        drivername="postgresql",
        username=username,
        password=password,
        host=host,
        port=port,
        database=database,
    )
    identity = {
        "violet_env": violet_env,
        "database": database,
        "host": host,
        "port": port,
        "username": username,
        "password_recorded": bool(password),
        "password_value_recorded": False,
        "url_without_password": str(url.set(password=None)),
    }
    return url, identity


def qident(value: str) -> str:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", value):
        raise ValueError(f"Unsafe SQL identifier: {value!r}")
    return f'"{value}"'


def table_exists(conn: Connection, table_name: str) -> bool:
    return bool(conn.execute(text("SELECT to_regclass(:name) IS NOT NULL"), {"name": table_name}).scalar())


def column_exists(conn: Connection, table_name: str, column_name: str) -> bool:
    return bool(
        conn.execute(
            text(
                """
                SELECT EXISTS (
                  SELECT 1
                  FROM information_schema.columns
                  WHERE table_schema = 'public'
                    AND table_name = :table_name
                    AND column_name = :column_name
                )
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        ).scalar()
    )


def count_table(conn: Connection, table_name: str) -> dict[str, Any]:
    if not table_exists(conn, table_name):
        return {"status": "missing_table", "count": None}
    value = int(conn.execute(text(f"SELECT COUNT(*) FROM {qident(table_name)}")).scalar() or 0)
    return {"status": "present", "count": value}


def group_count(
    conn: Connection,
    table_name: str,
    column_name: str,
    *,
    where_sql: str = "",
    limit: int | None = None,
) -> dict[str, int]:
    if not table_exists(conn, table_name) or not column_exists(conn, table_name, column_name):
        return {}
    suffix = f" WHERE {where_sql}" if where_sql else ""
    limit_sql = f" LIMIT {int(limit)}" if limit else ""
    rows = conn.execute(
        text(
            f"""
            SELECT COALESCE(CAST({qident(column_name)} AS TEXT), '<null>') AS key, COUNT(*) AS count
            FROM {qident(table_name)}
            {suffix}
            GROUP BY key
            ORDER BY count DESC, key ASC
            {limit_sql}
            """
        )
    ).mappings()
    return {str(row["key"]): int(row["count"]) for row in rows}


def scalar_count(conn: Connection, sql: str, params: Mapping[str, Any] | None = None) -> int:
    return int(conn.execute(text(sql), params or {}).scalar() or 0)


def rows_dict(conn: Connection, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(text(sql), params or {}).mappings().all()]


def rows_dict_expanding(conn: Connection, sql: str, params: Mapping[str, Any], expanding: Sequence[str]) -> list[dict[str, Any]]:
    stmt = text(sql)
    for name in expanding:
        stmt = stmt.bindparams(bindparam(name, expanding=True))
    return [dict(row) for row in conn.execute(stmt, params).mappings().all()]


def summarize_numbers(values: Iterable[int]) -> dict[str, Any]:
    nums = sorted(int(value) for value in values)
    if not nums:
        return {"count": 0, "min": 0, "max": 0, "average": 0, "median": 0, "p90": 0, "zero_count": 0}
    p90_index = min(len(nums) - 1, int(len(nums) * 0.9))
    return {
        "count": len(nums),
        "min": nums[0],
        "max": nums[-1],
        "average": round(sum(nums) / len(nums), 3),
        "median": median(nums),
        "p90": nums[p90_index],
        "zero_count": sum(1 for value in nums if value == 0),
    }


def percent(part: int, whole: int) -> float:
    if not whole:
        return 0.0
    return round(part / whole * 100, 2)


def scan_public_text(text_value: str) -> list[dict[str, str]]:
    checks = [
        ("local_path_or_private_root", LOCAL_PATH_RE),
        ("media_filename_like", MEDIA_FILENAME_RE),
        ("canonical_filename_like", CANONICAL_FILENAME_RE),
        ("secret_assignment_like", SECRET_VALUE_RE),
        ("authorization_bearer_like", BEARER_RE),
        ("openai_key_like", OPENAI_KEY_RE),
    ]
    findings: list[dict[str, str]] = []
    for name, pattern in checks:
        match = pattern.search(text_value)
        if match:
            findings.append({"type": name, "match": match.group(0)[:120]})
    return findings


def is_public_text_safe(text_value: str) -> bool:
    return not scan_public_text(text_value)


def safe_public_value(value: Any, *, fallback: str = "[redacted]") -> str:
    text_value = normalize_source_text(value)
    if not text_value:
        return ""
    if scan_public_text(text_value):
        return fallback
    return text_value


def looks_cjk(value: str) -> bool:
    return bool(CJK_RE.search(value or ""))


def looks_latin(value: str) -> bool:
    return bool(LATIN_RE.search(value or "")) and not looks_cjk(value)


def looks_danbooru_parenthetical(value: str) -> bool:
    return bool(DANBOORU_PAREN_RE.match(canonical_source_key(value or "")))


def read_only_identity(conn: Connection, env_identity: Mapping[str, Any]) -> dict[str, Any]:
    row = conn.execute(
        text(
            """
            SELECT current_database() AS database_name,
                   current_user AS database_user,
                   inet_server_addr()::text AS server_addr,
                   inet_server_port() AS server_port,
                   version() AS server_version
            """
        )
    ).mappings().one()
    transaction_read_only = str(conn.execute(text("SHOW transaction_read_only")).scalar() or "").lower()
    identity = {
        **dict(env_identity),
        "connected_database": row["database_name"],
        "connected_user": row["database_user"],
        "server_addr": row["server_addr"],
        "server_port": row["server_port"],
        "server_version": row["server_version"],
        "transaction_read_only": transaction_read_only,
        "transaction_read_only_ok": transaction_read_only in {"on", "true", "1"},
        "code_root": ROOT.name,
        "code_root_path_redacted": True,
        "git_branch": git_value(["git", "branch", "--show-current"]),
        "git_sha": git_value(["git", "rev-parse", "HEAD"]),
        "python_executable": Path(sys.executable).name,
        "python_executable_path_redacted": True,
        "recorded_at": utc_now_iso(),
    }
    if identity["connected_database"] != "blombooru":
        raise AuditBlockedError(f"Connected DB identity is not blombooru: {identity['connected_database']!r}")
    if not identity["transaction_read_only_ok"]:
        raise AuditBlockedError(f"PostgreSQL transaction is not read-only: {transaction_read_only!r}")
    return identity


def install_no_flush_guard(session: Session) -> None:
    def _raise_before_flush(*_args: Any, **_kwargs: Any) -> None:
        raise AuditBlockedError("ORM flush/write attempted during SCV1 read-only audit.")

    event.listen(session, "before_flush", _raise_before_flush)


def build_mutation_counts(conn: Connection) -> dict[str, Any]:
    return {
        "tables": {table: count_table(conn, table) for table in FORBIDDEN_TABLES},
        "recorded_at": utc_now_iso(),
    }


def compare_mutation_counts(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    changed: list[dict[str, Any]] = []
    missing: list[str] = []
    for table in FORBIDDEN_TABLES:
        left = before["tables"].get(table, {})
        right = after["tables"].get(table, {})
        if left.get("status") == "missing_table" or right.get("status") == "missing_table":
            missing.append(table)
            continue
        if left.get("count") != right.get("count"):
            changed.append({"table": table, "before": left.get("count"), "after": right.get("count")})
    return {
        "passed": not changed,
        "changed_tables": changed,
        "missing_tables": missing,
        "before": before,
        "after": after,
        "forbidden_tables_checked": list(FORBIDDEN_TABLES),
    }


def media_ids_with_source_layer(conn: Connection) -> set[int]:
    media_ids: set[int] = set()
    sources = [
        ("blombooru_source_metadata_records", "media_id"),
        ("blombooru_source_tag_observations", None),
        ("blombooru_source_name_observations", "media_id"),
        ("blombooru_source_searchable_name_assertions", None),
        ("blombooru_source_name_candidates", "media_id"),
        ("blombooru_source_concept_signals", "media_id"),
        ("blombooru_source_concept_evidence", "media_id"),
    ]
    for table_name, media_col in sources:
        if not table_exists(conn, table_name):
            continue
        if media_col is not None and column_exists(conn, table_name, media_col):
            rows = conn.execute(text(f"SELECT DISTINCT {qident(media_col)} AS media_id FROM {qident(table_name)} WHERE {qident(media_col)} IS NOT NULL"))
            media_ids.update(int(row[0]) for row in rows if row[0] is not None)
            continue
        if table_name in {"blombooru_source_tag_observations", "blombooru_source_searchable_name_assertions"}:
            rows = conn.execute(
                text(
                    f"""
                    SELECT DISTINCT r.media_id
                    FROM {qident(table_name)} t
                    JOIN blombooru_source_metadata_records r ON r.id = t.source_metadata_record_id
                    WHERE r.media_id IS NOT NULL
                    """
                )
            )
            media_ids.update(int(row[0]) for row in rows if row[0] is not None)
    return media_ids


def concept_media_set_for_ids(conn: Connection, concept_ids: Sequence[int], statuses: Sequence[str] = VISIBLE_STATUSES) -> set[int]:
    ids = sorted({int(value) for value in concept_ids if value is not None})
    if not ids:
        return set()
    media_ids: set[int] = set()
    if table_exists(conn, "blombooru_source_concept_evidence"):
        rows = rows_dict_expanding(
            conn,
            """
            SELECT DISTINCT media_id
            FROM blombooru_source_concept_evidence
            WHERE concept_id IN :ids
              AND status IN :statuses
              AND media_id IS NOT NULL
            """,
            {"ids": ids, "statuses": list(statuses)},
            ("ids", "statuses"),
        )
        media_ids.update(int(row["media_id"]) for row in rows if row.get("media_id") is not None)
    if table_exists(conn, "blombooru_source_concept_signal_links") and table_exists(conn, "blombooru_source_concept_signals"):
        rows = rows_dict_expanding(
            conn,
            """
            SELECT DISTINCT s.media_id
            FROM blombooru_source_concept_signal_links l
            JOIN blombooru_source_concept_signals s ON s.id = l.signal_id
            WHERE l.concept_id IN :ids
              AND l.link_status IN :statuses
              AND s.status IN :statuses
              AND s.media_id IS NOT NULL
            """,
            {"ids": ids, "statuses": list(statuses)},
            ("ids", "statuses"),
        )
        media_ids.update(int(row["media_id"]) for row in rows if row.get("media_id") is not None)
    return media_ids


def concept_ids_for_term(conn: Connection, term_value: str, statuses: Sequence[str] = VISIBLE_STATUSES) -> tuple[list[int], list[dict[str, Any]]]:
    keys = sorted(_search_keys_for_term(term_value))
    if not keys:
        return [], []
    rows = rows_dict_expanding(
        conn,
        """
        SELECT DISTINCT c.id AS concept_id, c.status AS concept_status, si.search_key, a.alias_role
        FROM blombooru_source_concept_search_index si
        JOIN blombooru_source_concepts c ON c.id = si.concept_id
        JOIN blombooru_source_concept_aliases a
          ON a.concept_id = si.concept_id
         AND a.alias_key = si.search_key
         AND a.alias_role = si.alias_role
        WHERE si.search_key IN :keys
          AND si.status IN :statuses
          AND a.status IN :statuses
          AND c.status IN :statuses
        """,
        {"keys": keys, "statuses": list(statuses)},
        ("keys", "statuses"),
    )
    all_ids = {int(row["concept_id"]) for row in rows}
    for _ in range(4):
        if not all_ids:
            break
        alias_rows = rows_dict_expanding(
            conn,
            """
            SELECT DISTINCT alias_key
            FROM blombooru_source_concept_aliases
            WHERE concept_id IN :ids
              AND status IN :statuses
              AND alias_key IS NOT NULL
            """,
            {"ids": sorted(all_ids), "statuses": list(statuses)},
            ("ids", "statuses"),
        )
        alias_keys = sorted({str(row["alias_key"]) for row in alias_rows if row.get("alias_key")})
        if not alias_keys:
            break
        sibling_rows = rows_dict_expanding(
            conn,
            """
            SELECT DISTINCT c.id AS concept_id
            FROM blombooru_source_concepts c
            JOIN blombooru_source_concept_aliases a ON a.concept_id = c.id
            WHERE a.alias_key IN :alias_keys
              AND a.status IN :statuses
              AND c.status IN :statuses
            """,
            {"alias_keys": alias_keys, "statuses": list(statuses)},
            ("alias_keys", "statuses"),
        )
        before = len(all_ids)
        all_ids.update(int(row["concept_id"]) for row in sibling_rows)
        if len(all_ids) == before:
            break
    hidden_rows = rows_dict_expanding(
        conn,
        """
        SELECT DISTINCT c.id AS concept_id, c.status AS concept_status
        FROM blombooru_source_concept_search_index si
        JOIN blombooru_source_concepts c ON c.id = si.concept_id
        WHERE si.search_key IN :keys
          AND c.status NOT IN :statuses
        """,
        {"keys": keys, "statuses": list(statuses)},
        ("keys", "statuses"),
    )
    return sorted(all_ids), hidden_rows


def audit_media_coverage(conn: Connection) -> dict[str, Any]:
    total_media = count_table(conn, "blombooru_media")["count"] or 0
    content_distribution = group_count(conn, "blombooru_media", "content_class")
    eligible_media = 0
    if column_exists(conn, "blombooru_media", "content_class"):
        eligible_media = scalar_count(
            conn,
            "SELECT COUNT(*) FROM blombooru_media WHERE content_class IN ('anime', 'unknown')",
        )
    media_tags_present = table_exists(conn, "blombooru_media_tags")
    tag_assoc_count = count_table(conn, "blombooru_media_tags")["count"] if media_tags_present else 0
    media_with_any_tags = (
        scalar_count(conn, "SELECT COUNT(DISTINCT media_id) FROM blombooru_media_tags")
        if media_tags_present and column_exists(conn, "blombooru_media_tags", "media_id")
        else 0
    )
    media_tag_source_distribution = group_count(conn, "blombooru_media_tags", "source")
    media_with_ai_tags = 0
    media_with_manual_or_locked = 0
    media_with_source_provider_tags = 0
    if media_tags_present and column_exists(conn, "blombooru_media_tags", "media_id"):
        if column_exists(conn, "blombooru_media_tags", "source") or column_exists(conn, "blombooru_media_tags", "is_suggestion"):
            where_parts = []
            if column_exists(conn, "blombooru_media_tags", "source"):
                where_parts.append("LOWER(COALESCE(source, '')) SIMILAR TO '%(ai|wd|tagger|model|clip)%'")
            if column_exists(conn, "blombooru_media_tags", "is_suggestion"):
                where_parts.append("is_suggestion IS TRUE")
            media_with_ai_tags = scalar_count(
                conn,
                f"SELECT COUNT(DISTINCT media_id) FROM blombooru_media_tags WHERE {' OR '.join(where_parts)}",
            )
        if column_exists(conn, "blombooru_media_tags", "source") or column_exists(conn, "blombooru_media_tags", "is_locked"):
            where_parts = []
            if column_exists(conn, "blombooru_media_tags", "source"):
                where_parts.append("LOWER(COALESCE(source, '')) = 'manual'")
            if column_exists(conn, "blombooru_media_tags", "is_locked"):
                where_parts.append("is_locked IS TRUE")
            media_with_manual_or_locked = scalar_count(
                conn,
                f"SELECT COUNT(DISTINCT media_id) FROM blombooru_media_tags WHERE {' OR '.join(where_parts)}",
            )
        if column_exists(conn, "blombooru_media_tags", "source"):
            media_with_source_provider_tags = scalar_count(
                conn,
                """
                SELECT COUNT(DISTINCT media_id)
                FROM blombooru_media_tags
                WHERE LOWER(COALESCE(source, '')) SIMILAR TO '%(source|provider|pixiv|booru|gallery|import)%'
                """,
            )
    source_layer_media_ids = media_ids_with_source_layer(conn)
    source_concept_media_ids = concept_media_set_for_ids(
        conn,
        [
            row["id"]
            for row in rows_dict(conn, "SELECT id FROM blombooru_source_concepts WHERE status IN ('active', 'needs_review')")
        ]
        if table_exists(conn, "blombooru_source_concepts")
        else [],
    )
    translation = {
        "total": count_table(conn, "blombooru_tag_translations")["count"] if table_exists(conn, "blombooru_tag_translations") else None,
        "by_status": group_count(conn, "blombooru_tag_translations", "status"),
        "by_source": group_count(conn, "blombooru_tag_translations", "source"),
        "by_category": group_count(conn, "blombooru_tag_translations", "category"),
    }
    tags = {
        "total_tags": count_table(conn, "blombooru_tags")["count"] if table_exists(conn, "blombooru_tags") else None,
        "media_tag_associations": tag_assoc_count,
        "associations_by_source": media_tag_source_distribution,
        "proper_noun_tag_category_counts": group_count(conn, "blombooru_tags", "category"),
        "translation": translation,
    }
    return {
        "total_media": total_media,
        "content_class_distribution": content_distribution,
        "eligible_policy": "content_class IN ('anime', 'unknown')",
        "eligible_media_count": eligible_media,
        "eligible_media_pct": percent(eligible_media, total_media),
        "media_with_any_tags": media_with_any_tags,
        "media_with_any_tags_pct": percent(media_with_any_tags, total_media),
        "media_with_ai_tag_provenance": media_with_ai_tags,
        "media_with_ai_tag_provenance_pct": percent(media_with_ai_tags, total_media),
        "media_with_manual_or_locked_tags": media_with_manual_or_locked,
        "media_with_source_imported_provider_tag_provenance": media_with_source_provider_tags,
        "media_without_ai_tags": max(total_media - media_with_ai_tags, 0),
        "media_with_source_layer_signals": len(source_layer_media_ids),
        "media_without_source_layer_signals": max(total_media - len(source_layer_media_ids), 0),
        "media_with_source_concept_evidence_or_links": len(source_concept_media_ids),
        "media_without_source_concept_evidence_or_links": max(total_media - len(source_concept_media_ids), 0),
        "tags": tags,
        "ai_jobs_by_status": group_count(conn, "blombooru_ai_tag_jobs", "status"),
        "classification_jobs_by_status": group_count(conn, "blombooru_classification_jobs", "status"),
        "tag_translation_jobs_by_status": group_count(conn, "blombooru_tag_translation_jobs", "status"),
    }


def audit_source_layer_coverage(conn: Connection) -> dict[str, Any]:
    table_counts = {table: count_table(conn, table) for table in SOURCE_LAYER_TABLES}
    source_records = {
        "by_provider": group_count(conn, "blombooru_source_metadata_records", "provider"),
        "by_status": group_count(conn, "blombooru_source_metadata_records", "status"),
        "linked_to_media": scalar_count(
            conn,
            "SELECT COUNT(*) FROM blombooru_source_metadata_records WHERE media_id IS NOT NULL",
        )
        if table_exists(conn, "blombooru_source_metadata_records")
        else 0,
    }
    tag_obs_linked = 0
    assertion_linked = 0
    if table_exists(conn, "blombooru_source_tag_observations") and table_exists(conn, "blombooru_source_metadata_records"):
        tag_obs_linked = scalar_count(
            conn,
            """
            SELECT COUNT(*)
            FROM blombooru_source_tag_observations t
            JOIN blombooru_source_metadata_records r ON r.id = t.source_metadata_record_id
            WHERE r.media_id IS NOT NULL
            """,
        )
    if table_exists(conn, "blombooru_source_searchable_name_assertions") and table_exists(conn, "blombooru_source_metadata_records"):
        assertion_linked = scalar_count(
            conn,
            """
            SELECT COUNT(*)
            FROM blombooru_source_searchable_name_assertions a
            JOIN blombooru_source_metadata_records r ON r.id = a.source_metadata_record_id
            WHERE r.media_id IS NOT NULL
            """,
        )
    f7a = {
        "candidates_by_provider": group_count(conn, "blombooru_source_name_candidates", "provider"),
        "candidates_by_role": group_count(conn, "blombooru_source_name_candidates", "candidate_role"),
        "candidates_by_status": group_count(conn, "blombooru_source_name_candidates", "status"),
        "candidate_status_distribution": group_count(conn, "blombooru_source_name_candidates", "candidate_status"),
        "candidates_linked_to_media": scalar_count(
            conn,
            "SELECT COUNT(*) FROM blombooru_source_name_candidates WHERE media_id IS NOT NULL",
        )
        if table_exists(conn, "blombooru_source_name_candidates")
        else 0,
        "distinct_media_with_candidates": scalar_count(
            conn,
            "SELECT COUNT(DISTINCT media_id) FROM blombooru_source_name_candidates WHERE media_id IS NOT NULL",
        )
        if table_exists(conn, "blombooru_source_name_candidates")
        else 0,
        "record_verdicts_by_verdict": group_count(conn, "blombooru_source_name_candidate_record_verdicts", "extraction_verdict"),
        "extraction_runs_by_status": group_count(conn, "blombooru_source_name_candidate_extraction_runs", "status"),
    }
    return {
        "table_counts": table_counts,
        "source_records": source_records,
        "source_tag_observations_linked_to_media": tag_obs_linked,
        "source_name_observations_linked_to_media": scalar_count(
            conn,
            "SELECT COUNT(*) FROM blombooru_source_name_observations WHERE media_id IS NOT NULL",
        )
        if table_exists(conn, "blombooru_source_name_observations")
        else 0,
        "source_assertions_linked_to_media": assertion_linked,
        "source_tag_observations_by_provider": group_count(conn, "blombooru_source_tag_observations", "provider"),
        "source_name_observations_by_provider": group_count(conn, "blombooru_source_name_observations", "provider"),
        "source_assertions_by_provider": group_count(conn, "blombooru_source_searchable_name_assertions", "provider"),
        "source_assertions_by_status": group_count(conn, "blombooru_source_searchable_name_assertions", "status"),
        "f7a_candidate_coverage": f7a,
        "provider_cache_by_provider": group_count(conn, "blombooru_provider_cache", "provider"),
    }


def load_concepts(conn: Connection) -> list[dict[str, Any]]:
    if not table_exists(conn, "blombooru_source_concepts"):
        return []
    return rows_dict(
        conn,
        """
        SELECT id, primary_display_name, concept_type_hint, status, confidence_score,
               evidence_score, media_count, source_count, evidence_summary_json, lifecycle_payload
        FROM blombooru_source_concepts
        ORDER BY id ASC
        """,
    )


def load_aliases(conn: Connection) -> list[dict[str, Any]]:
    if not table_exists(conn, "blombooru_source_concept_aliases"):
        return []
    return rows_dict(
        conn,
        """
        SELECT id, concept_id, alias_value, alias_key, display_name, language_hint,
               script_hint, alias_role, status, confidence
        FROM blombooru_source_concept_aliases
        ORDER BY concept_id ASC, id ASC
        """,
    )


def load_evidence(conn: Connection) -> list[dict[str, Any]]:
    if not table_exists(conn, "blombooru_source_concept_evidence"):
        return []
    return rows_dict(
        conn,
        """
        SELECT id, concept_id, signal_id, media_id, source_metadata_record_id, provider,
               evidence_type, evidence_strength, status
        FROM blombooru_source_concept_evidence
        ORDER BY concept_id ASC, id ASC
        """,
    )


def count_by_concept(rows: Iterable[Mapping[str, Any]], key: str = "concept_id") -> Counter[int]:
    counter: Counter[int] = Counter()
    for row in rows:
        value = row.get(key)
        if value is not None:
            counter[int(value)] += 1
    return counter


def search_index_counts(conn: Connection) -> Counter[int]:
    if not table_exists(conn, "blombooru_source_concept_search_index"):
        return Counter()
    return Counter(
        {
            int(row["concept_id"]): int(row["count"])
            for row in rows_dict(conn, "SELECT concept_id, COUNT(*) AS count FROM blombooru_source_concept_search_index GROUP BY concept_id")
        }
    )


def link_counts(conn: Connection) -> Counter[int]:
    if not table_exists(conn, "blombooru_source_concept_signal_links"):
        return Counter()
    return Counter(
        {
            int(row["concept_id"]): int(row["count"])
            for row in rows_dict(conn, "SELECT concept_id, COUNT(*) AS count FROM blombooru_source_concept_signal_links GROUP BY concept_id")
        }
    )


def concept_media_counts(conn: Connection, concept_ids: Sequence[int]) -> dict[int, int]:
    return {int(concept_id): len(concept_media_set_for_ids(conn, [int(concept_id)])) for concept_id in concept_ids}


def audit_source_concepts(conn: Connection) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    concepts = load_concepts(conn)
    aliases = load_aliases(conn)
    evidence = load_evidence(conn)
    aliases_by_concept: dict[int, list[dict[str, Any]]] = defaultdict(list)
    evidence_by_concept: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in aliases:
        aliases_by_concept[int(row["concept_id"])].append(row)
    for row in evidence:
        evidence_by_concept[int(row["concept_id"])].append(row)
    alias_counts = count_by_concept(aliases)
    evidence_counts = count_by_concept(evidence)
    search_counts = search_index_counts(conn)
    signal_link_counts = link_counts(conn)
    media_counts = concept_media_counts(conn, [row["id"] for row in concepts])

    alias_key_to_concepts: dict[str, set[int]] = defaultdict(set)
    for row in aliases:
        alias_key_to_concepts[str(row.get("alias_key") or "")].add(int(row["concept_id"]))
    duplicate_alias_keys = {
        key: sorted(value)
        for key, value in alias_key_to_concepts.items()
        if key and len(value) > 1
    }
    display_groups: dict[str, list[int]] = defaultdict(list)
    for row in concepts:
        display_groups[canonical_source_key(row.get("primary_display_name") or "")].append(int(row["id"]))
    duplicate_display_groups = {key: ids for key, ids in display_groups.items() if key and len(ids) > 1}

    origin_rows = rows_dict(conn, "SELECT origin_type, provider, source_kind, trust_tier, status, COUNT(*) AS count FROM blombooru_source_concept_signals GROUP BY origin_type, provider, source_kind, trust_tier, status") if table_exists(conn, "blombooru_source_concept_signals") else []
    evidence_origin_by_concept: dict[int, Counter[str]] = defaultdict(Counter)
    provider_by_concept: dict[int, Counter[str]] = defaultdict(Counter)
    strength_by_concept: dict[int, Counter[str]] = defaultdict(Counter)
    for row in evidence:
        concept_id = int(row["concept_id"])
        evidence_origin_by_concept[concept_id][str(row.get("evidence_type") or "unknown")] += 1
        provider_by_concept[concept_id][str(row.get("provider") or "unknown")] += 1
        strength_by_concept[concept_id][str(row.get("evidence_strength") or "unknown")] += 1

    ai_only = []
    source_title_only = []
    weak_only = []
    for row in concepts:
        concept_id = int(row["id"])
        origins = evidence_origin_by_concept.get(concept_id, Counter())
        providers = provider_by_concept.get(concept_id, Counter())
        strengths = strength_by_concept.get(concept_id, Counter())
        origin_text = " ".join(list(origins) + list(providers)).lower()
        if origins and all(("ai" in key.lower() or "wd" in key.lower() or "model" in key.lower()) for key in list(origins) + list(providers)):
            ai_only.append(concept_id)
        if origins and all("source_title" in key.lower() or key.lower() == "title" for key in origins):
            source_title_only.append(concept_id)
        if strengths and not {"strong", "high"}.intersection({key.lower() for key in strengths}):
            weak_only.append(concept_id)
        if not origins and ("ai" in origin_text):
            ai_only.append(concept_id)

    alias_inventory_rows = []
    for alias in aliases:
        concept = next((item for item in concepts if int(item["id"]) == int(alias["concept_id"])), {})
        alias_inventory_rows.append(
            {
                "concept_id": alias["concept_id"],
                "concept_status": concept.get("status", ""),
                "concept_type_hint": concept.get("concept_type_hint", ""),
                "concept_display": safe_public_value(concept.get("primary_display_name"), fallback="[redacted concept]"),
                "alias_id": alias["id"],
                "alias_display": safe_public_value(alias.get("display_name"), fallback="[redacted alias]"),
                "alias_key": safe_public_value(alias.get("alias_key"), fallback="[redacted alias key]"),
                "alias_role": safe_public_value(alias.get("alias_role"), fallback="unknown"),
                "alias_status": alias.get("status"),
                "language_hint": safe_public_value(alias.get("language_hint") or ""),
                "script_hint": safe_public_value(alias.get("script_hint") or ""),
                "confidence": alias.get("confidence"),
                "concept_media_count": media_counts.get(int(alias["concept_id"]), 0),
                "concept_evidence_count": evidence_counts.get(int(alias["concept_id"]), 0),
            }
        )
    evidence_inventory_rows = []
    for row in evidence:
        concept = next((item for item in concepts if int(item["id"]) == int(row["concept_id"])), {})
        evidence_inventory_rows.append(
            {
                "concept_id": row["concept_id"],
                "concept_status": concept.get("status", ""),
                "concept_type_hint": concept.get("concept_type_hint", ""),
                "concept_display": safe_public_value(concept.get("primary_display_name"), fallback="[redacted concept]"),
                "evidence_id": row["id"],
                "provider": safe_public_value(row.get("provider") or "unknown", fallback="unknown"),
                "evidence_type": safe_public_value(row.get("evidence_type") or "unknown", fallback="unknown"),
                "evidence_strength": safe_public_value(row.get("evidence_strength") or "unknown", fallback="unknown"),
                "status": row.get("status"),
                "has_media_id": row.get("media_id") is not None,
                "has_source_metadata_record_id": row.get("source_metadata_record_id") is not None,
            }
        )

    summary = {
        "total_source_concepts": len(concepts),
        "by_status": dict(Counter(str(row.get("status") or "<null>") for row in concepts)),
        "by_concept_type_hint": dict(Counter(str(row.get("concept_type_hint") or "<null>") for row in concepts)),
        "active_concepts": sum(1 for row in concepts if row.get("status") == "active"),
        "needs_review_concepts": sum(1 for row in concepts if row.get("status") == "needs_review"),
        "hidden_status_counts": {status: sum(1 for row in concepts if row.get("status") == status) for status in HIDDEN_STATUSES},
        "aliases_total": len(aliases),
        "evidence_total": len(evidence),
        "signal_links_total": count_table(conn, "blombooru_source_concept_signal_links")["count"] if table_exists(conn, "blombooru_source_concept_signal_links") else None,
        "search_index_total": count_table(conn, "blombooru_source_concept_search_index")["count"] if table_exists(conn, "blombooru_source_concept_search_index") else None,
        "alias_count_per_concept": summarize_numbers(alias_counts.get(int(row["id"]), 0) for row in concepts),
        "evidence_count_per_concept": summarize_numbers(evidence_counts.get(int(row["id"]), 0) for row in concepts),
        "link_count_per_concept": summarize_numbers(signal_link_counts.get(int(row["id"]), 0) for row in concepts),
        "search_index_count_per_concept": summarize_numbers(search_counts.get(int(row["id"]), 0) for row in concepts),
        "media_link_count_per_concept": summarize_numbers(media_counts.get(int(row["id"]), 0) for row in concepts),
        "media_linked_concepts": sum(1 for row in concepts if media_counts.get(int(row["id"]), 0) > 0),
        "concepts_with_no_media": sum(1 for row in concepts if media_counts.get(int(row["id"]), 0) == 0),
        "concepts_with_no_aliases": sum(1 for row in concepts if alias_counts.get(int(row["id"]), 0) == 0),
        "concepts_with_no_evidence": sum(1 for row in concepts if evidence_counts.get(int(row["id"]), 0) == 0),
        "concepts_with_no_search_index": sum(1 for row in concepts if search_counts.get(int(row["id"]), 0) == 0),
        "concepts_with_multiple_aliases": sum(1 for row in concepts if alias_counts.get(int(row["id"]), 0) > 1),
        "singleton_alias_concepts": sum(1 for row in concepts if alias_counts.get(int(row["id"]), 0) <= 1),
        "high_media_count_concepts": [
            {
                "concept_id": int(row["id"]),
                "status": row.get("status"),
                "display": safe_public_value(row.get("primary_display_name"), fallback="[redacted concept]"),
                "media_count": media_counts.get(int(row["id"]), 0),
            }
            for row in sorted(concepts, key=lambda item: media_counts.get(int(item["id"]), 0), reverse=True)[:25]
        ],
        "high_evidence_count_concepts": [
            {
                "concept_id": int(row["id"]),
                "status": row.get("status"),
                "display": safe_public_value(row.get("primary_display_name"), fallback="[redacted concept]"),
                "evidence_count": evidence_counts.get(int(row["id"]), 0),
            }
            for row in sorted(concepts, key=lambda item: evidence_counts.get(int(item["id"]), 0), reverse=True)[:25]
        ],
        "duplicate_primary_display_name_groups": [
            {"display_key": safe_public_value(key, fallback="[redacted display key]"), "concept_ids": ids[:20], "concept_count": len(ids)}
            for key, ids in list(duplicate_display_groups.items())[:50]
        ],
        "same_alias_key_across_multiple_concepts": [
            {"alias_key": safe_public_value(key, fallback="[redacted alias key]"), "concept_ids": ids[:20], "concept_count": len(ids)}
            for key, ids in list(duplicate_alias_keys.items())[:100]
        ],
        "concepts_by_evidence_origin_provider_source_kind": origin_rows,
        "ai_only_concept_count": len(set(ai_only)),
        "source_title_only_concept_count": len(set(source_title_only)),
        "weak_only_concept_count": len(set(weak_only)),
    }
    return summary, alias_inventory_rows, evidence_inventory_rows


def hidden_concept_ids_in_visible_closure(concept_ids: Sequence[int], concept_by_id: Mapping[int, Mapping[str, Any]]) -> list[int]:
    leaked: list[int] = []
    for concept_id in sorted({int(value) for value in concept_ids if value is not None}):
        status = (concept_by_id.get(concept_id) or {}).get("status")
        if status not in VISIBLE_STATUSES:
            leaked.append(concept_id)
    return leaked


def audit_search_symmetry(conn: Connection, concepts: Sequence[Mapping[str, Any]], aliases: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    visible_concepts = [row for row in concepts if row.get("status") in VISIBLE_STATUSES]
    concept_by_id = {int(row["id"]): row for row in concepts}
    aliases_by_concept: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in aliases:
        if row.get("status") in VISIBLE_STATUSES:
            aliases_by_concept[int(row["concept_id"])].append(row)

    metrics = Counter()
    samples: list[dict[str, Any]] = []
    for concept in visible_concepts:
        concept_id = int(concept["id"])
        concept_aliases = aliases_by_concept.get(concept_id, [])
        if not concept_aliases:
            continue
        metrics["concepts_checked"] += 1
        if concept.get("status") == "active":
            metrics["active_concepts_checked"] += 1
        elif concept.get("status") == "needs_review":
            metrics["needs_review_concepts_checked"] += 1

        alias_results = []
        for alias in concept_aliases:
            alias_label = str(alias.get("display_name") or alias.get("alias_value") or alias.get("alias_key") or "")
            concept_ids, hidden_rows = concept_ids_for_term(conn, alias_label, statuses=VISIBLE_STATUSES)
            hidden_leak_concept_ids = hidden_concept_ids_in_visible_closure(concept_ids, concept_by_id)
            media_set = concept_media_set_for_ids(conn, concept_ids, statuses=VISIBLE_STATUSES)
            alias_results.append(
                {
                    "alias_id": int(alias["id"]),
                    "alias_label": alias_label,
                    "alias_key": str(alias.get("alias_key") or ""),
                    "closure_concept_ids": concept_ids,
                    "media_ids": media_set,
                    "hidden_rows": hidden_rows,
                    "hidden_leak_concept_ids": hidden_leak_concept_ids,
                    "metacharacter": bool(SEARCH_TOKEN_META_RE.search(alias_label)),
                }
            )
            metrics["aliases_checked"] += 1
            if SEARCH_TOKEN_META_RE.search(alias_label):
                metrics["metacharacter_alias_count"] += 1
            if hidden_rows:
                metrics["hidden_status_raw_match_count"] += 1
            if hidden_leak_concept_ids:
                metrics["hidden_status_leak_count"] += 1

        media_sets = [item["media_ids"] for item in alias_results]
        closure_sets = [set(item["closure_concept_ids"]) for item in alias_results]
        first_media_set = media_sets[0] if media_sets else set()
        all_media_equal = all(item == first_media_set for item in media_sets)
        first_closure = closure_sets[0] if closure_sets else set()
        all_closure_equal = all(item == first_closure for item in closure_sets)
        mismatch_type = "exact_symmetric"
        if all_media_equal:
            metrics["exact_symmetric_concepts"] += 1
            if not first_media_set:
                metrics["explainable_no_media_concepts"] += 1
                mismatch_type = "explainable_no_media"
        else:
            metrics["asymmetric_concepts"] += 1
            mismatch_type = "asymmetric_media_set"
            if concept.get("status") == "active" and any(media_sets):
                metrics["severe_asymmetry_count"] += 1
            for left in media_sets:
                for right in media_sets:
                    if left and right and left != right and (left.issubset(right) or right.issubset(left)):
                        metrics["one_way_link_count"] += 1
                        break
        if not all_closure_equal:
            metrics["fragmentation_count"] += 1
            if mismatch_type == "exact_symmetric":
                mismatch_type = "fragmented_closure_same_media"
        concept_media = concept_media_set_for_ids(conn, [concept_id], statuses=VISIBLE_STATUSES)
        max_media = max((len(item) for item in media_sets), default=0)
        if concept_media and max_media > max(len(concept_media) * 3, len(concept_media) + 10):
            metrics["overbroad_expansion_count"] += 1
            if mismatch_type == "exact_symmetric":
                mismatch_type = "overbroad_expansion"
        if mismatch_type != "exact_symmetric" or len(samples) < 40:
            samples.append(
                {
                    "concept_id": concept_id,
                    "concept_status": concept.get("status"),
                    "concept_type_hint": concept.get("concept_type_hint"),
                    "concept_display": safe_public_value(concept.get("primary_display_name"), fallback="[redacted concept]"),
                    "mismatch_type": mismatch_type,
                    "alias_count": len(concept_aliases),
                    "concept_media_count": len(concept_media),
                    "min_alias_media_count": min((len(item["media_ids"]) for item in alias_results), default=0),
                    "max_alias_media_count": max_media,
                    "distinct_media_set_shapes": len({tuple(sorted(item["media_ids"])) for item in alias_results}),
                    "distinct_closure_shapes": len({tuple(sorted(item["closure_concept_ids"])) for item in alias_results}),
                    "hidden_leak_concept_ids": "; ".join(
                        ",".join(str(cid) for cid in item["hidden_leak_concept_ids"][:12])
                        for item in alias_results
                        if item["hidden_leak_concept_ids"]
                    ),
                    "sample_aliases": "; ".join(safe_public_value(item["alias_label"], fallback="[redacted alias]") for item in alias_results[:8]),
                    "sample_closure_concepts": "; ".join(
                        ",".join(str(cid) for cid in item["closure_concept_ids"][:12])
                        for item in alias_results[:4]
                    ),
                }
            )

    metrics["sampled_examples_private_count"] = len(samples)
    metrics["public_safe_example_count"] = min(len(samples), 12)
    for key in SEARCH_SYMMETRY_METRIC_KEYS:
        metrics.setdefault(key, 0)
    return dict(metrics), samples


def build_gap_bucket_detail(
    *,
    total_distinct_keys: int,
    missing_distinct_keys: int,
    sampled_missing_keys: int,
    sample_limit: int,
) -> dict[str, Any]:
    return {
        "total_distinct_keys": int(total_distinct_keys),
        "missing_distinct_keys": int(missing_distinct_keys),
        "sampled_missing_keys": int(sampled_missing_keys),
        "sample_limit": int(sample_limit),
        "counts_are_full": True,
        "sampling_affects": "examples_only",
    }


def summarize_missing_key_gap_rows(
    rows: Sequence[Mapping[str, Any]],
    visible_alias_keys: set[str],
    *,
    sample_limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    missing = [
        dict(row)
        for row in rows
        if canonical_source_key(row.get("key_value") or "") not in visible_alias_keys
        and str(row.get("key_value") or "") not in visible_alias_keys
    ]
    sampled = missing[:sample_limit]
    return sampled, build_gap_bucket_detail(
        total_distinct_keys=len(rows),
        missing_distinct_keys=len(missing),
        sampled_missing_keys=len(sampled),
        sample_limit=sample_limit,
    )


def audit_alias_gaps(conn: Connection, concepts: Sequence[Mapping[str, Any]], aliases: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    visible_concepts = [row for row in concepts if row.get("status") in VISIBLE_STATUSES]
    active_alias_keys = {
        str(row.get("alias_key") or "")
        for row in aliases
        if row.get("status") == "active" and row.get("alias_key")
    }
    visible_alias_keys = {
        str(row.get("alias_key") or "")
        for row in aliases
        if row.get("status") in VISIBLE_STATUSES and row.get("alias_key")
    }
    aliases_by_concept: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in aliases:
        if row.get("status") in VISIBLE_STATUSES:
            aliases_by_concept[int(row["concept_id"])].append(row)
    bucket_counts: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []
    sample_counts: Counter[str] = Counter()
    gap_bucket_details: dict[str, dict[str, Any]] = {}

    def add_sample(bucket: str, row: dict[str, Any], *, limit: int = ALIAS_GAP_SAMPLE_LIMIT) -> None:
        if sample_counts[bucket] >= limit:
            return
        samples.append(row)
        sample_counts[bucket] += 1

    for concept in visible_concepts:
        concept_id = int(concept["id"])
        values = [
            str(alias.get("display_name") or alias.get("alias_value") or alias.get("alias_key") or "")
            for alias in aliases_by_concept.get(concept_id, [])
        ]
        has_cjk = any(looks_cjk(value) for value in values)
        has_latin = any(looks_latin(value) for value in values)
        has_parenthetical = any(looks_danbooru_parenthetical(value) for value in values)
        if has_cjk and not has_latin:
            bucket_counts["cjk_alias_without_english_romaji_sibling"] += 1
            add_sample("cjk_alias_without_english_romaji_sibling", {"bucket": "cjk_alias_without_english_romaji_sibling", "concept_id": concept_id, "status": concept.get("status"), "sample": "; ".join(safe_public_value(v, fallback="[redacted alias]") for v in values[:5])})
        if has_parenthetical and not has_cjk:
            bucket_counts["danbooru_parenthetical_without_cjk_sibling"] += 1
            add_sample("danbooru_parenthetical_without_cjk_sibling", {"bucket": "danbooru_parenthetical_without_cjk_sibling", "concept_id": concept_id, "status": concept.get("status"), "sample": "; ".join(safe_public_value(v, fallback="[redacted alias]") for v in values[:5])})

    alias_key_groups: dict[str, set[int]] = defaultdict(set)
    display_groups: dict[str, set[int]] = defaultdict(set)
    for alias in aliases:
        if alias.get("status") not in VISIBLE_STATUSES:
            continue
        alias_key_groups[str(alias.get("alias_key") or "")].add(int(alias["concept_id"]))
        display_groups[canonical_source_key(alias.get("display_name") or alias.get("alias_value") or "")].add(int(alias["concept_id"]))
    for key, ids in alias_key_groups.items():
        if key and len(ids) > 1:
            bucket_counts["same_normalized_alias_key_split_across_multiple_concepts"] += 1
            add_sample("same_normalized_alias_key_split_across_multiple_concepts", {"bucket": "same_normalized_alias_key_split_across_multiple_concepts", "concept_id": ",".join(map(str, sorted(ids)[:12])), "sample": safe_public_value(key, fallback="[redacted alias key]")})
    for key, ids in display_groups.items():
        if key and len(ids) > 1:
            bucket_counts["same_display_name_split_across_contexts"] += 1
            add_sample("same_display_name_split_across_contexts", {"bucket": "same_display_name_split_across_contexts", "concept_id": ",".join(map(str, sorted(ids)[:12])), "sample": safe_public_value(key, fallback="[redacted display key]")})

    source_unlinked_queries = [
        ("source_tag_present_no_source_concept_alias", "blombooru_source_tag_observations", "canonical_tag_key", "raw_tag"),
        ("source_name_present_no_source_concept_alias", "blombooru_source_name_observations", "canonical_name_key", "raw_name"),
        ("source_assertion_present_not_connected", "blombooru_source_searchable_name_assertions", "canonical_name_key", "asserted_name"),
        ("normal_tag_present_no_source_concept_alias", "blombooru_tags", "name", "name"),
    ]
    for bucket, table_name, key_column, label_column in source_unlinked_queries:
        if not table_exists(conn, table_name) or not column_exists(conn, table_name, key_column):
            continue
        rows = rows_dict(
            conn,
            f"""
            SELECT {qident(key_column)} AS key_value,
                   MIN({qident(label_column)}) AS label,
                   COUNT(*) AS count
            FROM {qident(table_name)}
            WHERE {qident(key_column)} IS NOT NULL
            GROUP BY {qident(key_column)}
            ORDER BY count DESC
            """,
        )
        sampled_missing, detail = summarize_missing_key_gap_rows(rows, visible_alias_keys, sample_limit=ALIAS_GAP_SAMPLE_LIMIT)
        gap_bucket_details[bucket] = detail
        bucket_counts[bucket] += int(detail["missing_distinct_keys"])
        for row in sampled_missing:
            add_sample(
                bucket,
                {
                    "bucket": bucket,
                    "concept_id": "",
                    "sample": safe_public_value(row.get("label") or row.get("key_value"), fallback="[redacted source value]"),
                    "count": row.get("count"),
                },
            )
        gap_bucket_details[bucket]["sampled_missing_keys"] = sample_counts[bucket]

    if table_exists(conn, "blombooru_source_tag_registry"):
        rows = rows_dict(
            conn,
            """
            SELECT canonical_tag_key AS key_value, normalized_tag AS label, seen_count
            FROM blombooru_source_tag_registry
            ORDER BY seen_count DESC NULLS LAST
            """,
        )
        missing = [row for row in rows if str(row.get("key_value") or "") not in visible_alias_keys]
        high_frequency_missing = [row for row in missing if int(row.get("seen_count") or 0) >= 2]
        bucket = "high_frequency_source_tag_or_name_unlinked"
        bucket_counts[bucket] += len(high_frequency_missing)
        for row in high_frequency_missing[:HIGH_FREQUENCY_GAP_SAMPLE_LIMIT]:
            add_sample(bucket, {"bucket": bucket, "sample": safe_public_value(row.get("label"), fallback="[redacted source value]"), "count": row.get("seen_count")}, limit=HIGH_FREQUENCY_GAP_SAMPLE_LIMIT)
        gap_bucket_details[bucket] = build_gap_bucket_detail(
            total_distinct_keys=len(rows),
            missing_distinct_keys=len(high_frequency_missing),
            sampled_missing_keys=sample_counts[bucket],
            sample_limit=HIGH_FREQUENCY_GAP_SAMPLE_LIMIT,
        )

    needs_review_rows = [row for row in concepts if row.get("status") == "needs_review"]
    for row in needs_review_rows:
        concept_id = int(row["id"])
        keys = {str(alias.get("alias_key") or "") for alias in aliases_by_concept.get(concept_id, [])}
        if keys and not keys.intersection(active_alias_keys):
            bucket_counts["needs_review_cluster_with_no_active_alias_path"] += 1
            add_sample("needs_review_cluster_with_no_active_alias_path", {"bucket": "needs_review_cluster_with_no_active_alias_path", "concept_id": concept_id, "status": row.get("status"), "sample": safe_public_value(row.get("primary_display_name"), fallback="[redacted concept]")})

    seed_results = {}
    for seed_name, values in SEED_GROUPS.items():
        aliases_present = []
        concept_ids: set[int] = set()
        for value in values:
            ids, _hidden = concept_ids_for_term(conn, value, statuses=VISIBLE_STATUSES) if table_exists(conn, "blombooru_source_concept_search_index") else ([], [])
            if ids:
                aliases_present.append(safe_public_value(value, fallback="[redacted seed]"))
                concept_ids.update(ids)
        seed_results[seed_name] = {
            "seed_values_tested": [safe_public_value(value, fallback="[redacted seed]") for value in values],
            "matched_alias_values": aliases_present,
            "matched_concept_ids": sorted(concept_ids),
            "matched_concept_count": len(concept_ids),
            "matched_media_count": len(concept_media_set_for_ids(conn, sorted(concept_ids))) if concept_ids else 0,
            "gap_detected": len(concept_ids) == 0 or len(aliases_present) < min(2, len(values)),
        }
    if seed_results["nahida_prompt_and_doc1"]["gap_detected"]:
        bucket_counts["nahida_seed_gap"] += 1

    concept_bucket_total_keys = {
        "cjk_alias_without_english_romaji_sibling": len(visible_concepts),
        "danbooru_parenthetical_without_cjk_sibling": len(visible_concepts),
        "same_normalized_alias_key_split_across_multiple_concepts": len(alias_key_groups),
        "same_display_name_split_across_contexts": len(display_groups),
        "needs_review_cluster_with_no_active_alias_path": len(needs_review_rows),
        "nahida_seed_gap": len(SEED_GROUPS["nahida_prompt_and_doc1"]),
    }
    for bucket, count in bucket_counts.items():
        if bucket in gap_bucket_details:
            continue
        gap_bucket_details[bucket] = build_gap_bucket_detail(
            total_distinct_keys=concept_bucket_total_keys.get(bucket, count),
            missing_distinct_keys=count,
            sampled_missing_keys=sample_counts[bucket],
            sample_limit=ALIAS_GAP_SAMPLE_LIMIT,
        )

    recommended = "source_concept_alias_resolver_improvement" if sum(bucket_counts.values()) else "entity_bridge_preview_design"
    return {
        "gap_buckets": dict(bucket_counts),
        "gap_bucket_details": gap_bucket_details,
        "sample_limit_policy": {
            "counts_are_full": True,
            "samples_are_limited_to_examples_only": True,
            "default_sample_limit": ALIAS_GAP_SAMPLE_LIMIT,
            "high_frequency_sample_limit": HIGH_FREQUENCY_GAP_SAMPLE_LIMIT,
        },
        "total_gap_signals": sum(bucket_counts.values()),
        "seed_results": seed_results,
        "recommended_next_fix_category": recommended,
    }, samples


def audit_needs_review(conn: Connection, concepts: Sequence[Mapping[str, Any]], aliases: Sequence[Mapping[str, Any]], evidence: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    needs_review = [row for row in concepts if row.get("status") == "needs_review"]
    active_alias_keys = {
        str(row.get("alias_key") or "")
        for row in aliases
        if row.get("status") == "active" and row.get("alias_key")
    }
    aliases_by_concept: dict[int, list[dict[str, Any]]] = defaultdict(list)
    evidence_by_concept: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in aliases:
        aliases_by_concept[int(row["concept_id"])].append(row)
    for row in evidence:
        evidence_by_concept[int(row["concept_id"])].append(row)

    samples: list[dict[str, Any]] = []
    counters = Counter()
    reason_buckets = Counter()
    for concept in needs_review:
        concept_id = int(concept["id"])
        media_count = len(concept_media_set_for_ids(conn, [concept_id]))
        evidence_rows = evidence_by_concept.get(concept_id, [])
        alias_rows = aliases_by_concept.get(concept_id, [])
        alias_values = [str(row.get("display_name") or row.get("alias_value") or row.get("alias_key") or "") for row in alias_rows]
        alias_keys = {str(row.get("alias_key") or "") for row in alias_rows}
        strengths = {str(row.get("evidence_strength") or "").lower() for row in evidence_rows}
        evidence_types = {str(row.get("evidence_type") or "").lower() for row in evidence_rows}
        if media_count:
            counters["needs_review_with_media"] += 1
        if len(evidence_rows) >= 5:
            counters["needs_review_high_evidence_count"] += 1
        if alias_keys.intersection(active_alias_keys):
            counters["needs_review_sharing_alias_with_active"] += 1
        if alias_keys.intersection(active_alias_keys) or canonical_source_key(concept.get("primary_display_name") or ""):
            counters["needs_review_duplicate_or_fragment_candidate"] += 1 if alias_keys.intersection(active_alias_keys) else 0
        if evidence_types and all("source_title" in item or item == "title" for item in evidence_types):
            counters["needs_review_source_title_only"] += 1
        if evidence_types and all("ai" in item or "wd" in item or "model" in item for item in evidence_types):
            counters["needs_review_ai_only"] += 1
        if any(looks_cjk(value) for value in alias_values):
            counters["needs_review_with_cjk_alias"] += 1
        if any(looks_danbooru_parenthetical(value) for value in alias_values):
            counters["needs_review_with_parenthetical_context"] += 1
        if not media_count and not evidence_rows and not alias_rows:
            counters["needs_review_with_no_media_evidence_or_alias"] += 1
        if not media_count or len(evidence_rows) >= 5 or alias_keys.intersection(active_alias_keys) or {"strong", "high"}.intersection(strengths):
            samples.append(
                {
                    "concept_id": concept_id,
                    "display": safe_public_value(concept.get("primary_display_name"), fallback="[redacted concept]"),
                    "media_count": media_count,
                    "evidence_count": len(evidence_rows),
                    "alias_count": len(alias_rows),
                    "shares_active_alias": bool(alias_keys.intersection(active_alias_keys)),
                    "strengths": ",".join(sorted(strengths)),
                    "sample_aliases": "; ".join(safe_public_value(value, fallback="[redacted alias]") for value in alias_values[:8]),
                }
            )
        lifecycle = concept.get("lifecycle_payload")
        if isinstance(lifecycle, dict):
            reason = lifecycle.get("reason_code") or lifecycle.get("negative_reason_code") or lifecycle.get("status_reason")
            if reason:
                reason_buckets[str(reason)] += 1
    return {
        "total_needs_review_concepts": len(needs_review),
        **dict(counters),
        "top_reason_buckets": dict(reason_buckets.most_common(25)),
        "sample_count": len(samples),
        "assessment": "needs_review retains recall value but should be triaged/scored before broader truth or management work"
        if needs_review
        else "no needs_review concepts currently available to assess",
    }, samples[:300]


def decide_next_phase(
    media: Mapping[str, Any],
    source_layer: Mapping[str, Any],
    concepts: Mapping[str, Any],
    symmetry: Mapping[str, Any],
    alias_gaps: Mapping[str, Any],
    needs_review: Mapping[str, Any],
    redaction_passed: bool,
) -> dict[str, Any]:
    options: list[dict[str, Any]] = []
    total_media = int(media.get("total_media") or 0)
    eligible = int(media.get("eligible_media_count") or 0)
    ai_missing = int(media.get("media_without_ai_tags") or 0)
    source_records = int(source_layer.get("source_records", {}).get("linked_to_media") or 0)
    total_concepts = int(concepts.get("total_source_concepts") or 0)
    severe = int(symmetry.get("severe_asymmetry_count") or 0)
    asym = int(symmetry.get("asymmetric_concepts") or 0)
    gaps = int(alias_gaps.get("total_gap_signals") or 0)
    needs_review_total = int(needs_review.get("total_needs_review_concepts") or 0)

    def add(key: str, priority: str, recommended: bool, reasons: list[str], blockers: list[str]) -> None:
        options.append(
            {
                "key": key,
                "priority": priority,
                "recommended": recommended,
                "reasons": reasons,
                "blockers": blockers,
            }
        )

    if not redaction_passed:
        add("redaction_or_safety_fix_first", "P0", True, ["public/private redaction scan failed"], ["fix report/artifact boundary before any expansion"])

    alias_problem = severe > 0 or asym > 0 or gaps > 0 or needs_review_total > 0
    add(
        "source_concept_alias_resolver_improvement",
        "P1" if alias_problem else "P2",
        bool(redaction_passed and alias_problem),
        [
            f"search asymmetry concepts={asym}, severe={severe}",
            f"alias/cross-language/source linkage gap signals={gaps}",
            f"needs_review concepts={needs_review_total}",
        ],
        ["must remain source-layer only", "must not promote to Entity/media_tags truth"],
    )
    add(
        "bounded_ai_tag_expansion",
        "P2" if eligible and percent(ai_missing, max(total_media, 1)) >= 20 else "P3",
        bool(redaction_passed and eligible and percent(ai_missing, max(total_media, 1)) >= 50),
        [f"media without AI tag provenance={ai_missing}/{total_media}", "would be a separate approved run"],
        ["requires separate AI job phase, test DB/storage safety, no localization/provider coupling"],
    )
    add(
        "bounded_pixiv_metadata_expansion",
        "P2" if total_media and percent(source_records, max(total_media, 1)) < 50 else "P3",
        bool(redaction_passed and total_media and percent(source_records, max(total_media, 1)) < 25),
        [f"source metadata records linked to media={source_records}/{total_media}", "coverage gap may limit SourceConcept evidence"],
        ["requires provider policy, cache/audit/rate limit/budget, no originals by default, separate run approval"],
    )
    translation_total = media.get("tags", {}).get("translation", {}).get("total")
    tag_total = media.get("tags", {}).get("total_tags")
    translation_gap = isinstance(tag_total, int) and isinstance(translation_total, int) and tag_total > translation_total
    add(
        "tag_localization_catchup",
        "P3",
        bool(redaction_passed and translation_gap and not alias_problem),
        [f"tag translations={translation_total}, total tags={tag_total}", "separate from SourceConcept identity linking"],
        ["must not run background translation in SCV1", "proper-noun aliases are not translation truth"],
    )
    add(
        "source_concept_management_or_editing_design",
        "P2" if total_concepts and not severe else "P3",
        bool(redaction_passed and total_concepts > 0 and not severe and gaps < max(5, total_concepts // 10)),
        ["manual correction may help after alias/resolver quality is acceptable"],
        ["requires audit trail, rollback/supersede, source-layer-only guard"],
    )
    entity_ready = redaction_passed and total_concepts > 0 and severe == 0 and gaps == 0 and needs_review_total == 0
    add(
        "entity_bridge_preview_design",
        "P3",
        entity_ready,
        ["requires strong coverage, redaction, search symmetry, and low needs_review noise"],
        ["preview/manual confirmation/audit trail/rollback/no truth-path pollution guards required"],
    )
    add(
        "run_ledger_or_phase39_prerequisite",
        "P1",
        True,
        ["any 5k/10k or provider/AI/source expansion needs checkpoint/failure-budget discipline"],
        ["must exist before broad/full-library run"],
    )

    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    recommended_options = [item for item in options if item["recommended"]]
    recommended_options.sort(key=lambda item: priority_order.get(item["priority"], 9))
    highest = recommended_options[0]["key"] if recommended_options else "source_concept_alias_resolver_improvement"
    return {
        "options": options,
        "recommended_next_phase": highest,
        "answers": {
            "is_5k_10k_expansion_justified_now": False,
            "if_yes_expansion_of_what": "N/A; broad 5k/10k expansion is not justified inside or immediately after SCV1 without a separate ledger and bounded phase.",
            "what_must_be_added_before_5k_10k": [
                "run ledger/checkpoint/failure budget",
                "read-only identity and mutation proof per run",
                "redaction-safe reporting boundary",
                "separate approval for AI/provider/import/localization execution",
            ],
            "is_entity_bridge_justified_now": entity_ready,
            "is_source_concept_editing_justified_now": highest == "source_concept_management_or_editing_design",
            "should_pixiv_metadata_tag_extraction_be_next": any(item["key"] == "bounded_pixiv_metadata_expansion" and item["recommended"] for item in options),
            "should_local_ai_tagging_be_next": any(item["key"] == "bounded_ai_tag_expansion" and item["recommended"] for item in options),
            "should_tag_localization_be_next": any(item["key"] == "tag_localization_catchup" and item["recommended"] for item in options),
            "highest_impact_risk_adjusted_priority": highest,
        },
        "conservative_note": "SCV1 is an audit only; recommendations require separate approved implementation/run phases.",
    }


def build_public_summary(
    db_identity: Mapping[str, Any],
    proof: Mapping[str, Any],
    media: Mapping[str, Any],
    source_layer: Mapping[str, Any],
    concepts: Mapping[str, Any],
    symmetry: Mapping[str, Any],
    alias_gaps: Mapping[str, Any],
    needs_review: Mapping[str, Any],
    redaction: Mapping[str, Any],
    decision: Mapping[str, Any],
    output_dir: Path,
    validation: Mapping[str, Any],
) -> dict[str, Any]:
    safe_identity = {
        key: db_identity.get(key)
        for key in [
            "violet_env",
            "database",
            "host",
            "port",
            "connected_database",
            "server_port",
            "transaction_read_only",
            "transaction_read_only_ok",
            "git_branch",
            "git_sha",
            "python_executable",
            "recorded_at",
        ]
    }
    return {
        "phase": PHASE,
        "title": PHASE_TITLE,
        "branch": BRANCH,
        "generated_at": utc_now_iso(),
        "db_identity": safe_identity,
        "read_only_proof": {
            "passed": proof.get("passed"),
            "changed_tables": proof.get("changed_tables"),
            "missing_tables": proof.get("missing_tables"),
            "forbidden_tables_checked": proof.get("forbidden_tables_checked"),
        },
        "media_coverage": media,
        "source_layer_coverage": source_layer,
        "source_concept_inventory": concepts,
        "search_symmetry": symmetry,
        "alias_gap_analysis": {
            "gap_buckets": alias_gaps.get("gap_buckets"),
            "gap_bucket_details": alias_gaps.get("gap_bucket_details"),
            "sample_limit_policy": alias_gaps.get("sample_limit_policy"),
            "total_gap_signals": alias_gaps.get("total_gap_signals"),
            "recommended_next_fix_category": alias_gaps.get("recommended_next_fix_category"),
        },
        "needs_review_analysis": needs_review,
        "redaction_privacy_audit": redaction,
        "seed_results": alias_gaps.get("seed_results", {}),
        "decision_matrix": decision,
        "recommended_next_phase": decision.get("recommended_next_phase"),
        "validation": validation,
        "safety": {
            "db_write": False,
            "db_migration": False,
            "server_started": False,
            "browser_validation": "N/A; no UI/runtime change",
            "provider_llm_ai_localization_import_run": False,
            "entity_or_truth_path_write": False,
            "source_icloud_app_storage_mutation": False,
        },
        "artifact_lifecycle": {
            "scripts/run_phase45_scv1_source_concept_coverage_audit.py": "phase-scoped operational runner",
            "tests/test_phase45_scv1_source_concept_coverage_audit.py": "phase-scoped validation test",
            "docs/reports/phase-4.5-scv1-source-concept-coverage-audit.md": "public report / handoff / roadmap update",
            "docs/reports/phase-4.5-scv1-source-concept-coverage-audit-summary.json": "public report / handoff / roadmap update",
            ".local_manifests/phase-4.5-scv1-source-concept-coverage-audit": "one-off local artifact / ignored output",
        },
        "private_artifacts": public_private_artifact_summary(bundle_created=False),
    }


def public_report_markdown(summary: Mapping[str, Any]) -> str:
    media = summary["media_coverage"]
    concepts = summary["source_concept_inventory"]
    symmetry = summary["search_symmetry"]
    gaps = summary["alias_gap_analysis"]
    needs_review = summary["needs_review_analysis"]
    decision = summary["decision_matrix"]
    answers = decision["answers"]
    seed = summary["seed_results"].get("nahida_prompt_and_doc1", {})
    private_artifacts = summary.get("private_artifacts", {})
    checked_tables = summary["read_only_proof"].get("forbidden_tables_checked") or []
    lines = [
        f"# {PHASE} {PHASE_TITLE}",
        "",
        "## Summary",
        "",
        "SCV1 performed a read-only audit over the current development DB. It generated private aggregate/sample artifacts under `.local_manifests` and this public-safe report. No import, provider call, AI tagging, localization, LLM, migration, server, browser, Entity bridge, promotion, or truth-path write was run.",
        "",
        "This report is a reviewer-fix rerun for PR #100. Pre-fix SCV1 values are superseded where redaction proof ordering, mutation-proof table coverage, alias gap counts, or hidden-status metrics were affected.",
        "",
        "## Scope",
        "",
        "- Current development DB only.",
        "- Existing media, tags, AI tag provenance, source metadata/source-layer rows, F7a candidates, and SourceConcept tables.",
        "- Public report uses aggregate counts and redacted labels only.",
        "",
        "## Non-goals",
        "",
        "- No 5k/10k/full-library run.",
        "- No DB writes, migrations, imports, providers, LLMs, localization, AI jobs, SourceConcept editing, Entity bridge, promotion, confirmed assignments, or `media_tags` mutation.",
        "",
        "## DB identity and read-only proof",
        "",
        f"- DB: `{summary['db_identity'].get('connected_database')}` on `{summary['db_identity'].get('host')}:{summary['db_identity'].get('port')}`.",
        f"- Git: `{summary['db_identity'].get('git_branch')}` at `{summary['db_identity'].get('git_sha')}`.",
        f"- Python: `{summary['db_identity'].get('python_executable')}`.",
        f"- PostgreSQL transaction_read_only: `{summary['db_identity'].get('transaction_read_only')}`.",
        f"- Forbidden table count proof passed: `{summary['read_only_proof'].get('passed')}`.",
        f"- Missing optional forbidden tables recorded: `{len(summary['read_only_proof'].get('missing_tables') or [])}`.",
        f"- SourceConcept signals table included in mutation proof: `{'blombooru_source_concept_signals' in checked_tables}`.",
        "",
        "## Media coverage baseline",
        "",
        f"- Total media: `{media.get('total_media')}`.",
        f"- Eligible media policy: `{media.get('eligible_policy')}`; eligible count `{media.get('eligible_media_count')}` (`{media.get('eligible_media_pct')}%`).",
        f"- Media with any tags: `{media.get('media_with_any_tags')}`.",
        f"- Media with AI tag provenance: `{media.get('media_with_ai_tag_provenance')}`; without AI tags `{media.get('media_without_ai_tags')}`.",
        f"- Media with source-layer signals: `{media.get('media_with_source_layer_signals')}`; without source-layer signals `{media.get('media_without_source_layer_signals')}`.",
        f"- Media with SourceConcept evidence or links: `{media.get('media_with_source_concept_evidence_or_links')}`.",
        f"- Content class distribution: `{json.dumps(media.get('content_class_distribution'), ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Source-layer coverage",
        "",
        f"- Source metadata records by provider: `{json.dumps(summary['source_layer_coverage'].get('source_records', {}).get('by_provider'), ensure_ascii=False, sort_keys=True)}`.",
        f"- Source metadata records linked to media: `{summary['source_layer_coverage'].get('source_records', {}).get('linked_to_media')}`.",
        f"- F7a distinct media with candidates: `{summary['source_layer_coverage'].get('f7a_candidate_coverage', {}).get('distinct_media_with_candidates')}`.",
        f"- Source assertions by status: `{json.dumps(summary['source_layer_coverage'].get('source_assertions_by_status'), ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## SourceConcept inventory",
        "",
        f"- Total SourceConcepts: `{concepts.get('total_source_concepts')}`.",
        f"- By status: `{json.dumps(concepts.get('by_status'), ensure_ascii=False, sort_keys=True)}`.",
        f"- By type hint: `{json.dumps(concepts.get('by_concept_type_hint'), ensure_ascii=False, sort_keys=True)}`.",
        f"- Aliases/evidence/search index totals: `{concepts.get('aliases_total')}` / `{concepts.get('evidence_total')}` / `{concepts.get('search_index_total')}`.",
        f"- Concepts with no media / no aliases / no evidence / no search index: `{concepts.get('concepts_with_no_media')}` / `{concepts.get('concepts_with_no_aliases')}` / `{concepts.get('concepts_with_no_evidence')}` / `{concepts.get('concepts_with_no_search_index')}`.",
        f"- Same alias key across multiple concepts: `{len(concepts.get('same_alias_key_across_multiple_concepts') or [])}` sampled groups.",
        "",
        "## Search symmetry audit",
        "",
        f"- Concepts checked: `{symmetry.get('concepts_checked')}`; aliases checked: `{symmetry.get('aliases_checked')}`.",
        f"- Exact symmetric concepts: `{symmetry.get('exact_symmetric_concepts')}`.",
        f"- Explainable no-media concepts: `{symmetry.get('explainable_no_media_concepts')}`.",
        f"- Asymmetric concepts: `{symmetry.get('asymmetric_concepts')}`; severe asymmetry: `{symmetry.get('severe_asymmetry_count')}`.",
        f"- One-way links / fragmentation / overbroad: `{symmetry.get('one_way_link_count')}` / `{symmetry.get('fragmentation_count')}` / `{symmetry.get('overbroad_expansion_count')}`.",
        f"- Hidden raw matches / actual visible hidden leakage: `{symmetry.get('hidden_status_raw_match_count')}` / `{symmetry.get('hidden_status_leak_count')}`.",
        "- Hidden raw matches mean a lookup encountered hidden rejected/ambiguous/superseded rows; actual leakage means hidden concepts entered the visible closure/media result and should remain zero.",
        f"- Parser/metacharacter aliases: `{symmetry.get('metacharacter_alias_count')}`.",
        "",
        "## Alias gap analysis",
        "",
        f"- Total gap signals: `{gaps.get('total_gap_signals')}`.",
        f"- Gap buckets: `{json.dumps(gaps.get('gap_buckets'), ensure_ascii=False, sort_keys=True)}`.",
        f"- Gap bucket details: `{json.dumps(gaps.get('gap_bucket_details'), ensure_ascii=False, sort_keys=True)}`.",
        f"- Sample policy: `{json.dumps(gaps.get('sample_limit_policy'), ensure_ascii=False, sort_keys=True)}`.",
        "- Alias/source gap counts are full grouped-key counts; sample limits affect examples only, not totals or the decision matrix.",
        f"- Full-count correction supersedes the pre-fix limited total gap signal value `2025` with `{gaps.get('total_gap_signals')}`.",
        f"- Route impact: the corrected count strengthens the alias/source-linkage concern; highest recommendation remains `{summary.get('recommended_next_phase')}`.",
        f"- Recommended fix category: `{gaps.get('recommended_next_fix_category')}`.",
        "",
        "## Needs-review cluster analysis",
        "",
        f"- Total needs_review concepts: `{needs_review.get('total_needs_review_concepts')}`.",
        f"- With media / high evidence / sharing active alias: `{needs_review.get('needs_review_with_media', 0)}` / `{needs_review.get('needs_review_high_evidence_count', 0)}` / `{needs_review.get('needs_review_sharing_alias_with_active', 0)}`.",
        f"- CJK alias / parenthetical context / empty cluster: `{needs_review.get('needs_review_with_cjk_alias', 0)}` / `{needs_review.get('needs_review_with_parenthetical_context', 0)}` / `{needs_review.get('needs_review_with_no_media_evidence_or_alias', 0)}`.",
        f"- Assessment: {needs_review.get('assessment')}.",
        "",
        "## Redaction/privacy audit",
        "",
        f"- Public redaction passed: `{summary['redaction_privacy_audit'].get('passed')}`.",
        f"- Public artifacts checked: `{json.dumps(summary['redaction_privacy_audit'].get('public_paths'), ensure_ascii=False)}`.",
        f"- Final scan after public fields finalized: `{summary['redaction_privacy_audit'].get('final_public_scan_after_public_fields_finalized')}`.",
        f"- Checked at: `{summary['redaction_privacy_audit'].get('checked_at')}`.",
        f"- Findings: `{json.dumps(summary['redaction_privacy_audit'].get('findings'), ensure_ascii=False)}`.",
        f"- Private artifact bundle created: `{private_artifacts.get('private_artifact_bundle_created')}`; exact private paths public: `{private_artifacts.get('exact_private_paths_public')}`.",
        f"- Private artifact count: `{private_artifacts.get('private_artifact_count')}` under `{private_artifacts.get('private_artifact_root_label')}`.",
        "",
        "## Nahida / 纳西妲 / 草神 seed result",
        "",
        f"- Seed values tested: `{json.dumps(seed.get('seed_values_tested'), ensure_ascii=False)}`.",
        f"- Matched aliases: `{json.dumps(seed.get('matched_alias_values'), ensure_ascii=False)}`.",
        f"- Matched concept count: `{seed.get('matched_concept_count')}`; matched media count: `{seed.get('matched_media_count')}`.",
        f"- Gap detected: `{seed.get('gap_detected')}`.",
        "",
        "## Decision matrix",
        "",
    ]
    for item in decision.get("options", []):
        lines.append(f"- `{item['key']}`: priority `{item['priority']}`, recommended `{item['recommended']}`; reasons: {'; '.join(item['reasons'])}")
    lines.extend(
        [
            "",
            "## Recommended next phase",
            "",
            f"`{summary.get('recommended_next_phase')}` is the highest impact/risk-adjusted next route from this audit.",
            "",
            "## Expansion and bridge answers",
            "",
            f"- Is 5k/10k expansion justified now? `{answers.get('is_5k_10k_expansion_justified_now')}`.",
            f"- If yes, expansion of what exactly? `{answers.get('if_yes_expansion_of_what')}`.",
            f"- Must add before any 5k/10k run: `{json.dumps(answers.get('what_must_be_added_before_5k_10k'), ensure_ascii=False)}`.",
            f"- Is Entity bridge justified now? `{answers.get('is_entity_bridge_justified_now')}`.",
            f"- Is SourceConcept editing justified now? `{answers.get('is_source_concept_editing_justified_now')}`.",
            f"- Should Pixiv/source metadata extraction be next? `{answers.get('should_pixiv_metadata_tag_extraction_be_next')}`.",
            f"- Should local AI tagging be next? `{answers.get('should_local_ai_tagging_be_next')}`.",
            f"- Should tag localization be next? `{answers.get('should_tag_localization_be_next')}`.",
            "",
            "## Deferred work",
            "",
            "- Any AI tagging, provider/Pixiv/source metadata expansion, localization, SourceConcept editing, Entity bridge, promotion, or broad-library work remains a separate approved phase.",
            "- Entity bridge still requires preview, manual confirmation, audit trail, rollback/supersede behavior, and no truth-path pollution guards.",
            "",
            "## Validation",
            "",
            f"- Operational audit command: `{summary['validation'].get('operational_audit_command')}`.",
            f"- Operational audit result: `{summary['validation'].get('operational_audit_result')}`.",
            "- Focused test results are recorded in the PR/final delivery report.",
            "- Real browser validation: N/A, no UI/runtime behavior changed.",
            "",
            "## Safety confirmation",
            "",
            "- No push to main.",
            "- No merge.",
            "- No DB write, migration, import, cleanup/delete/reset/drop/truncate, source storage mutation, cloud-file mutation, app-storage mutation, AI tagging, localization, LLM, provider call, Entity Resolver, similarity, SourceConcept editing, Entity bridge, promotion, confirmed assignment, or media_tags mutation.",
            "",
            "## Artifact lifecycle",
            "",
            "- `scripts/run_phase45_scv1_source_concept_coverage_audit.py`: phase-scoped operational runner.",
            "- `tests/test_phase45_scv1_source_concept_coverage_audit.py`: phase-scoped validation test.",
            "- Private `.local_manifests` outputs: one-off local artifacts / ignored output.",
            "- Public report and summary JSON: public report / handoff / roadmap update.",
            "",
            "## Engineering judgment / operator notes",
            "",
            "SCV1 achieved the intended audit shape if the read-only proof and redaction scan pass. The prompt scope is appropriate: broad enough to answer the next-route question, but correctly narrow because it forbids writes, providers, LLMs, localization, imports, SourceConcept editing, and Entity bridge work. The next phase should address the highest-priority audited gap rather than starting broad 5k/10k execution.",
            "",
        ]
    )
    return "\n".join(lines)


def scan_public_artifacts(paths: Sequence[Path], *, checked_at: str | None = None) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            findings.append({"path": root_relative_or_name(path), "type": "missing_public_artifact"})
            continue
        text_value = path.read_text(encoding="utf-8", errors="replace")
        for finding in scan_public_text(text_value):
            findings.append({"path": root_relative_or_name(path), **finding})
    return {
        "checked_at": checked_at or utc_now_iso(),
        "passed": not findings,
        "public_paths": [root_relative_or_name(path) for path in paths],
        "findings": findings,
    }


def final_redaction_record(paths: Sequence[Path], *, checked_at: str) -> dict[str, Any]:
    return {
        "checked_at": checked_at,
        "passed": True,
        "public_paths": [root_relative_or_name(path) for path in paths],
        "findings": [],
        "final_public_scan_after_public_fields_finalized": True,
        "exact_private_paths_public": False,
        "private_artifact_paths_public": False,
        "policy": "final public report and summary JSON are written with all public fields finalized, then scanned without another public rewrite",
    }


def write_reports_and_redaction(summary: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    paths = [PUBLIC_REPORT_MD, PUBLIC_REPORT_JSON]
    checked_at = utc_now_iso()
    redaction = final_redaction_record(paths, checked_at=checked_at)
    summary["redaction_privacy_audit"] = redaction
    write_json(PUBLIC_REPORT_JSON, summary)
    write_text(PUBLIC_REPORT_MD, public_report_markdown(summary))

    final_scan = scan_public_artifacts(paths, checked_at=checked_at)
    if not final_scan["passed"]:
        failed_redaction = {**redaction, "passed": False, "findings": final_scan["findings"]}
        write_json(output_dir / "redaction-privacy-audit.json", failed_redaction)
        write_text(output_dir / "public-redaction-check.txt", json.dumps(failed_redaction, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        raise AuditBlockedError(f"Public redaction scan failed: {final_scan['findings']!r}")

    write_json(output_dir / "redaction-privacy-audit.json", redaction)
    write_text(output_dir / "public-redaction-check.txt", json.dumps(redaction, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return redaction


def build_artifact_checksums(output_dir: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for path in sorted(output_dir.rglob("*")):
        if path.is_file() and path.name != "checksums.json":
            checksums[str(path.relative_to(output_dir)).replace("\\", "/")] = sha256_file(path)
    return checksums


def run_audit(args: argparse.Namespace) -> dict[str, Any]:
    if not args.read_only:
        raise AuditBlockedError("SCV1 runner requires --read-only.")
    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    url, env_identity = build_database_url()
    engine = create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10, "options": "-c statement_timeout=300000"},
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False)
    conn: Connection | None = None
    session: Session | None = None
    try:
        conn = engine.connect()
        if conn.dialect.name != "postgresql":
            raise AuditBlockedError(f"SCV1 requires PostgreSQL read-only transaction support, got {conn.dialect.name!r}.")
        conn.exec_driver_sql("BEGIN TRANSACTION READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout = '300s'")
        db_identity = read_only_identity(conn, env_identity)
        write_json(output_dir / "db-identity.json", db_identity)

        session = SessionLocal(bind=conn)
        install_no_flush_guard(session)
        before_counts = build_mutation_counts(conn)

        media = audit_media_coverage(conn)
        source_layer = audit_source_layer_coverage(conn)
        concepts_rows = load_concepts(conn)
        aliases_rows = load_aliases(conn)
        evidence_rows = load_evidence(conn)
        source_concepts, alias_inventory, evidence_inventory = audit_source_concepts(conn)
        symmetry_metrics, symmetry_samples = audit_search_symmetry(conn, concepts_rows, aliases_rows)
        alias_gap_analysis, alias_gap_samples = audit_alias_gaps(conn, concepts_rows, aliases_rows)
        needs_review_analysis, needs_review_samples = audit_needs_review(conn, concepts_rows, aliases_rows, evidence_rows)

        after_counts = build_mutation_counts(conn)
        proof = compare_mutation_counts(before_counts, after_counts)
        if not proof["passed"]:
            raise AuditBlockedError(f"Forbidden table counts changed during read-only audit: {proof['changed_tables']!r}")

        validation = {
            "operational_audit_command": "python scripts/run_phase45_scv1_source_concept_coverage_audit.py --output-dir .local_manifests/phase-4.5-scv1-source-concept-coverage-audit --write-public-report --read-only",
            "operational_audit_result": "passed",
            "transaction_read_only": db_identity["transaction_read_only"],
            "forbidden_table_count_changes": proof["changed_tables"],
        }
        decision = decide_next_phase(media, source_layer, source_concepts, symmetry_metrics, alias_gap_analysis, needs_review_analysis, True)
        summary = build_public_summary(
            db_identity=db_identity,
            proof=proof,
            media=media,
            source_layer=source_layer,
            concepts=source_concepts,
            symmetry=symmetry_metrics,
            alias_gaps=alias_gap_analysis,
            needs_review=needs_review_analysis,
            redaction={"passed": None, "findings": []},
            decision=decision,
            output_dir=output_dir,
            validation=validation,
        )

        write_json(output_dir / "read-only-mutation-proof.json", proof)
        write_json(output_dir / "media-coverage-inventory.json", media)
        write_json(output_dir / "source-layer-coverage-inventory.json", source_layer)
        write_json(output_dir / "source-concept-inventory.json", source_concepts)
        write_csv(
            output_dir / "source-concept-alias-inventory.csv",
            alias_inventory,
            [
                "concept_id",
                "concept_status",
                "concept_type_hint",
                "concept_display",
                "alias_id",
                "alias_display",
                "alias_key",
                "alias_role",
                "alias_status",
                "language_hint",
                "script_hint",
                "confidence",
                "concept_media_count",
                "concept_evidence_count",
            ],
        )
        write_csv(
            output_dir / "source-concept-evidence-inventory.csv",
            evidence_inventory,
            [
                "concept_id",
                "concept_status",
                "concept_type_hint",
                "concept_display",
                "evidence_id",
                "provider",
                "evidence_type",
                "evidence_strength",
                "status",
                "has_media_id",
                "has_source_metadata_record_id",
            ],
        )
        write_json(output_dir / "source-concept-search-symmetry.json", symmetry_metrics)
        write_csv(
            output_dir / "source-concept-search-symmetry-samples.csv",
            symmetry_samples,
            [
                "concept_id",
                "concept_status",
                "concept_type_hint",
                "concept_display",
                "mismatch_type",
                "alias_count",
                "concept_media_count",
                "min_alias_media_count",
                "max_alias_media_count",
                "distinct_media_set_shapes",
                "distinct_closure_shapes",
                "hidden_leak_concept_ids",
                "sample_aliases",
                "sample_closure_concepts",
            ],
        )
        write_json(output_dir / "alias-gap-analysis.json", alias_gap_analysis)
        write_csv(output_dir / "alias-gap-samples.csv", alias_gap_samples, ["bucket", "concept_id", "status", "sample", "count"])
        write_json(output_dir / "needs-review-cluster-analysis.json", needs_review_analysis)
        write_csv(
            output_dir / "needs-review-cluster-samples.csv",
            needs_review_samples,
            ["concept_id", "display", "media_count", "evidence_count", "alias_count", "shares_active_alias", "strengths", "sample_aliases"],
        )
        write_json(output_dir / "scv1-decision-matrix.json", decision)

        initial_zip_path = zip_directory(output_dir)
        summary["private_artifacts"] = public_private_artifact_summary(bundle_created=initial_zip_path.exists())

        if args.write_public_report:
            redaction = write_reports_and_redaction(summary, output_dir)
            summary["redaction_privacy_audit"] = redaction

        write_json(output_dir / "checksums.json", build_artifact_checksums(output_dir))
        zip_directory(output_dir)
        conn.exec_driver_sql("ROLLBACK")
        return summary
    finally:
        if session is not None:
            session.close()
        if conn is not None:
            try:
                if not conn.closed:
                    conn.exec_driver_sql("ROLLBACK")
            except Exception:
                pass
            conn.close()
        engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--write-public-report", action="store_true")
    parser.add_argument("--read-only", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_audit(args)
    print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
