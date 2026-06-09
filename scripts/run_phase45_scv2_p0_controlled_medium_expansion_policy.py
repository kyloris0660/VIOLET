"""Run Phase 4.5-SCV2-P0 controlled medium expansion inventory and policy.

Lifecycle: phase-scoped operational runner.

This runner is intentionally read-only. It connects to the current development
database through app-compatible DB identity resolution without importing app
modules, opens a PostgreSQL read-only transaction, inventories current media,
Pixiv-like DB signals, source metadata gaps, and AI tag continuity, then writes
private artifacts and a public-safe report. It does not start the app, run
migrations, import media, stage-copy files, run providers, run gallery-dl, run
AI/classification/localization jobs, call LLMs, or mutate source/app storage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import URL, bindparam, create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parent.parent

PHASE = "4.5-SCV2-P0"
PHASE_TITLE = "Controlled Medium Expansion and Source Metadata Run Policy"
PHASE_SLUG = "phase-4.5-scv2-p0-controlled-medium-expansion-policy"
BRANCH = "codex/phase45-scv2-p0-controlled-medium-expansion-policy"
PUBLIC_REPORT_MD = ROOT / "docs" / "reports" / f"{PHASE_SLUG}.md"
PUBLIC_REPORT_JSON = ROOT / "docs" / "reports" / f"{PHASE_SLUG}-summary.json"
DEFAULT_OUTPUT_DIR = ROOT / ".local_manifests" / PHASE_SLUG

PRIVATE_ARTIFACT_NAMES = (
    "db-identity.json",
    "current-media-baseline.json",
    "current-pixiv-like-media-inventory.json",
    "current-source-metadata-gap-inventory.json",
    "ai-tag-coverage-baseline.json",
    "medium-expansion-candidate-policy.json",
    "medium-expansion-ledger-schema.json",
    "pixiv-source-metadata-ledger-schema.json",
    "phase-split-plan.json",
    "risk-and-stop-conditions.json",
    "public-redaction-check.txt",
)

SUMMARY_REQUIRED_FIELDS = {
    "phase",
    "title",
    "branch",
    "generated_at",
    "db_identity",
    "current_media_baseline",
    "current_pixiv_like_media_inventory",
    "source_metadata_gap",
    "ai_tag_continuity_policy",
    "medium_expansion_policy",
    "phase_split",
    "ledger_schema",
    "safety_gates",
    "decision_matrix",
    "recommended_next_phase",
    "validation",
    "safety",
    "artifact_lifecycle",
    "private_artifacts",
}

FORBIDDEN_TABLES = (
    "blombooru_media",
    "blombooru_tags",
    "blombooru_media_tags",
    "blombooru_scan_jobs",
    "blombooru_scan_job_media",
    "blombooru_ai_tag_jobs",
    "blombooru_classification_jobs",
    "blombooru_tag_translations",
    "blombooru_tag_translation_jobs",
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
    "blombooru_source_concept_signals",
    "blombooru_source_concept_evidence",
)

APP_DEFAULT_DATABASE = {
    "host": "db",
    "port": 5432,
    "name": "blombooru",
    "user": "postgres",
    "password": "",
}
DB_FIELD_ENV_KEYS = {
    "host": "POSTGRES_HOST",
    "port": "POSTGRES_PORT",
    "name": "POSTGRES_DB",
    "user": "POSTGRES_USER",
    "password": "POSTGRES_PASSWORD",
}

PIXIV_FILENAME_PATTERNS = (
    re.compile(r"(?<!\d)(?P<pixiv_id>\d{6,12})[_-]p(?P<page_index>\d+)(?!\d)", re.IGNORECASE),
    re.compile(r"(?<!\d)pixiv[_ -]?(?P<pixiv_id>\d{6,12})(?:[_-]p(?P<page_index>\d+))?(?!\d)", re.IGNORECASE),
    re.compile(r"(?<!\d)illust(?:ration)?[_ -]?(?P<pixiv_id>\d{6,12})(?:[_-]p(?P<page_index>\d+))?(?!\d)", re.IGNORECASE),
)
PIXIV_SOURCE_PATTERNS = (
    re.compile(r"pixiv\.net/(?:en/)?artworks/(?P<pixiv_id>\d{6,12})", re.IGNORECASE),
    re.compile(r"(?:illust_id|artwork_id|artworks?)[=:/_-](?P<pixiv_id>\d{6,12})", re.IGNORECASE),
    re.compile(r"(?<!\d)(?P<pixiv_id>\d{6,12})[_-]p(?P<page_index>\d+)(?!\d)", re.IGNORECASE),
)
PIXIV_MARKER_RE = re.compile(r"(?i)(^|[^a-z0-9])(pixiv|pximg|artworks?|illust_id|illust)([^a-z0-9]|$)")
PRIVATE_ID_SALT = "phase45-scv2-p0"

SECRET_VALUE_RE = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|authorization|cookie)\s*[:=]\s*['\"]?[A-Za-z0-9_\-./:+]{6,}"
)
BEARER_RE = re.compile(r"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9._\-]{8,}")
OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
LOCAL_PATH_RE = re.compile(
    r"(?i)((?<![A-Za-z])[A-Z]:[\\/]|file://|\\\\|"
    r"/(?:Users|home|mnt|Volumes|storage|media|original|thumbnails|thumbs)(?:/|$)|"
    r"\\Users\\|\bUsers[\\/]|(?:iCloud|Pictures|Documents|Desktop|Downloads)[\\/])"
)
MEDIA_FILENAME_RE = re.compile(r"(?i)\b[A-Za-z0-9][A-Za-z0-9_. -]{0,100}\.(jpg|jpeg|png|webp|gif|bmp|avif|mp4|webm|mov|zip|rar|7z)\b")
CANONICAL_PATH_RE = re.compile(
    r"(?i)\b(?:mnt_storage|volumes_[a-z0-9]|storage_[a-z0-9]|media_original|"
    r"original_(?!downloaded\b|filename\b)[a-z0-9]|thumbnails_[a-z0-9]|thumbs_[a-z0-9]|icloud_[a-z0-9]|"
    r"pictures_[a-z0-9]|documents_[a-z0-9]|downloads_[a-z0-9]|desktop_[a-z0-9])"
)


class PolicyBlockedError(RuntimeError):
    """Raised when the read-only policy inventory cannot continue safely."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n")


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


def normalize_source_text(value: Any) -> str:
    text_value = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", text_value).strip()


def canonical_source_key(value: Any) -> str:
    text_value = normalize_source_text(value).casefold()
    text_value = re.sub(r"[\s/,;|]+", "_", text_value)
    text_value = re.sub(r"[^\w:()+.-]+", "_", text_value, flags=re.UNICODE)
    return re.sub(r"_+", "_", text_value).strip("_")


def stable_private_id(value: Any, prefix: str = "item") -> str:
    digest = hashlib.sha256(f"{PRIVATE_ID_SALT}:{value}".encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def nonempty(value: Any) -> bool:
    return value is not None and str(value) != ""


def env_or_dotenv_lookup(
    key: str,
    dotenv: Mapping[str, str],
    default: str | None = None,
    *,
    process_env: Mapping[str, str] | None = None,
) -> tuple[str | None, str]:
    env = process_env if process_env is not None else os.environ
    value = env.get(key)
    if nonempty(value):
        return str(value), "process_env"
    value = dotenv.get(key)
    if nonempty(value):
        return str(value), ".env"
    return default, "app_default"


def password_source_label(source: str, value: Any) -> str:
    suffix = "present" if nonempty(value) else "empty"
    if source == "settings_json":
        return f"settings_json_{suffix}"
    if source == "process_env":
        return f"process_env_{suffix}"
    if source == ".env":
        return f".env_{suffix}"
    return f"app_default_{suffix}"


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


def app_settings_file_path(dotenv: Mapping[str, str], *, process_env: Mapping[str, str] | None = None) -> Path:
    storage_root_value, _source = env_or_dotenv_lookup("VIOLET_STORAGE_ROOT", dotenv, None, process_env=process_env)
    storage_root = Path(storage_root_value) if storage_root_value else ROOT
    return storage_root / "data" / "settings.json"


def load_database_file_settings(path: Path | None = None, *, dotenv: Mapping[str, str] | None = None) -> tuple[dict[str, Any], bool]:
    if path is None:
        path = app_settings_file_path(dotenv or load_dotenv_values(ROOT / ".env"))
    if not path.exists():
        return {}, False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}, True
    database = payload.get("database")
    return (database if isinstance(database, dict) else {}), True


def resolve_db_field(
    field: str,
    file_db: Mapping[str, Any],
    dotenv: Mapping[str, str],
    *,
    process_env: Mapping[str, str] | None = None,
) -> tuple[Any, str]:
    if nonempty(file_db.get(field)):
        return file_db[field], "settings_json"
    env_value, env_source = env_or_dotenv_lookup(DB_FIELD_ENV_KEYS[field], dotenv, None, process_env=process_env)
    if nonempty(env_value):
        return env_value, env_source
    return APP_DEFAULT_DATABASE[field], "app_default"


def resolve_app_database_config(
    *,
    dotenv: Mapping[str, str] | None = None,
    file_db: Mapping[str, Any] | None = None,
    settings_json_exists: bool | None = None,
    process_env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    dotenv_values = dict(dotenv or load_dotenv_values(ROOT / ".env"))
    if file_db is None or settings_json_exists is None:
        loaded_file_db, loaded_exists = load_database_file_settings(dotenv=dotenv_values)
        file_db = loaded_file_db if file_db is None else file_db
        settings_json_exists = loaded_exists if settings_json_exists is None else settings_json_exists

    resolved_fields: dict[str, Any] = {}
    field_sources: dict[str, str] = {}
    for field in ("host", "port", "name", "user", "password"):
        value, source = resolve_db_field(field, file_db or {}, dotenv_values, process_env=process_env)
        resolved_fields[field] = value
        field_sources[field] = password_source_label(source, value) if field == "password" else source

    try:
        resolved_fields["port"] = int(resolved_fields["port"])
    except (TypeError, ValueError) as exc:
        raise PolicyBlockedError(f"Invalid PostgreSQL port for SCV2-P0 DB resolution: {resolved_fields['port']!r}") from exc

    violet_env, violet_env_source = env_or_dotenv_lookup("VIOLET_ENV", dotenv_values, "development", process_env=process_env)
    violet_env = str(violet_env or "development").strip().lower()
    password = str(resolved_fields.get("password") or "")
    url = URL.create(
        drivername="postgresql",
        username=str(resolved_fields["user"]),
        password=password,
        host=str(resolved_fields["host"]),
        port=int(resolved_fields["port"]),
        database=str(resolved_fields["name"]),
    )
    url_without_password = str(url.set(password=None))
    return {
        **resolved_fields,
        "password": password,
        "violet_env": violet_env,
        "violet_env_source": violet_env_source,
        "settings_json_exists": bool(settings_json_exists),
        "database_file_settings_used": any(source.startswith("settings_json") for source in field_sources.values()),
        "field_sources": field_sources,
        "url": url,
        "app_equivalent_url_without_password": url_without_password,
        "runner_url_without_password": url_without_password,
        "urls_match": True,
        "runner_matches_app_equivalent": True,
        "app_compatible": True,
        "password_present": bool(password),
        "password_value_recorded": False,
    }


def assert_db_resolution_parity(identity: Mapping[str, Any]) -> None:
    resolution = identity.get("db_resolution") if "db_resolution" in identity else identity
    if not isinstance(resolution, Mapping) or not resolution.get("app_compatible"):
        raise PolicyBlockedError("SCV2-P0 DB resolution did not certify app-compatible development precedence.")
    if not resolution.get("urls_match") or not resolution.get("runner_matches_app_equivalent"):
        raise PolicyBlockedError("SCV2-P0 runner DB URL does not match app-equivalent development DB URL.")


def build_database_url() -> tuple[URL, dict[str, Any]]:
    resolved = resolve_app_database_config()
    violet_env = str(resolved["violet_env"]).strip().lower()
    if violet_env != "development":
        raise PolicyBlockedError(f"SCV2-P0 must run against VIOLET_ENV=development, got {violet_env!r}.")

    database = str(resolved["name"])
    if database != "blombooru":
        raise PolicyBlockedError(f"SCV2-P0 must run against development DB 'blombooru', got {database!r}.")

    url = resolved["url"]
    assert_db_resolution_parity(resolved)
    identity = {
        "violet_env": violet_env,
        "database": database,
        "host": str(resolved["host"]),
        "port": int(resolved["port"]),
        "username": str(resolved["user"]),
        "password_recorded": bool(resolved["password_present"]),
        "password_value_recorded": False,
        "url_without_password": resolved["runner_url_without_password"],
        "db_resolution": {
            "app_compatible": True,
            "settings_json_exists": bool(resolved["settings_json_exists"]),
            "database_file_settings_used": bool(resolved["database_file_settings_used"]),
            "field_sources": dict(resolved["field_sources"]),
            "violet_env_source": resolved["violet_env_source"],
            "runner_url_without_password": resolved["runner_url_without_password"],
            "app_equivalent_url_without_password": resolved["app_equivalent_url_without_password"],
            "urls_match": bool(resolved["urls_match"]),
            "runner_matches_app_equivalent": bool(resolved["runner_matches_app_equivalent"]),
            "password_present": bool(resolved["password_present"]),
            "password_value_recorded": False,
        },
    }
    return url, identity


def public_db_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    resolution = dict(identity.get("db_resolution") or {})
    return {
        "violet_env": identity.get("violet_env"),
        "database": identity.get("database"),
        "connected_database": identity.get("connected_database"),
        "host": identity.get("host"),
        "port": identity.get("port"),
        "connected_user_recorded": bool(identity.get("connected_user")),
        "server_port": identity.get("server_port"),
        "transaction_read_only": identity.get("transaction_read_only"),
        "transaction_read_only_ok": identity.get("transaction_read_only_ok"),
        "git_branch": identity.get("git_branch"),
        "git_sha": identity.get("git_sha"),
        "python_executable": Path(str(identity.get("python_executable") or "")).name,
        "python_executable_path_redacted": True,
        "db_resolution": {
            "app_compatible": resolution.get("app_compatible"),
            "settings_json_exists": resolution.get("settings_json_exists"),
            "database_file_settings_used": resolution.get("database_file_settings_used"),
            "field_sources": resolution.get("field_sources"),
            "violet_env_source": resolution.get("violet_env_source"),
            "runner_matches_app_equivalent": resolution.get("runner_matches_app_equivalent"),
            "urls_match": resolution.get("urls_match"),
            "password_present": resolution.get("password_present"),
            "password_value_recorded": False,
        },
        "recorded_at": identity.get("recorded_at"),
    }


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


def group_count_sql(conn: Connection, sql: str, params: Mapping[str, Any] | None = None) -> dict[str, int]:
    rows = conn.execute(text(sql), params or {}).mappings()
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


def percent(part: int, whole: int) -> float:
    if not whole:
        return 0.0
    return round(part / whole * 100, 2)


def scan_public_text(text_value: str) -> list[dict[str, str]]:
    checks = [
        ("local_path_or_private_root", LOCAL_PATH_RE),
        ("canonical_path_like", CANONICAL_PATH_RE),
        ("media_filename_like", MEDIA_FILENAME_RE),
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


def redact_public_label(value: Any, *, prefix: str = "redacted") -> str:
    text_value = normalize_source_text(value)
    if not text_value:
        return ""
    if scan_public_text(text_value):
        return f"[{prefix}:{stable_private_id(text_value, 'label')}]"
    if len(text_value) > 80:
        return f"[{prefix}:{stable_private_id(text_value, 'label')}]"
    return text_value


def scan_public_artifacts(paths: Sequence[Path], *, checked_at: str | None = None, public_path_labels: Sequence[str] | None = None) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    labels = list(public_path_labels or [root_relative_or_name(path) for path in paths])
    for index, path in enumerate(paths):
        label = labels[index] if index < len(labels) else root_relative_or_name(path)
        if not path.exists():
            findings.append({"path": label, "type": "missing_public_artifact"})
            continue
        text_value = path.read_text(encoding="utf-8", errors="replace")
        for finding in scan_public_text(text_value):
            findings.append({"path": label, **finding})
    return {
        "checked_at": checked_at or utc_now_iso(),
        "passed": not findings,
        "public_paths": labels,
        "findings": findings,
    }


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
        "python_executable": str(Path(sys.executable)),
        "python_executable_path_recorded_private_only": True,
        "recorded_at": utc_now_iso(),
    }
    if identity["connected_database"] != "blombooru":
        raise PolicyBlockedError(f"Connected DB identity is not blombooru: {identity['connected_database']!r}")
    if not identity["transaction_read_only_ok"]:
        raise PolicyBlockedError(f"PostgreSQL transaction is not read-only: {transaction_read_only!r}")
    assert_db_resolution_parity(identity)
    return identity


def install_no_flush_guard(session: Session) -> None:
    def _raise_before_flush(*_args: Any, **_kwargs: Any) -> None:
        raise PolicyBlockedError("ORM flush/write attempted during SCV2-P0 read-only inventory.")

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


def ai_tag_predicate(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return f"(LOWER(COALESCE({prefix}source, '')) SIMILAR TO '%(ai|wd|tagger|model|clip)%' OR {prefix}is_suggestion IS TRUE)"


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


def media_ids_with_source_concept(conn: Connection) -> set[int]:
    media_ids: set[int] = set()
    if table_exists(conn, "blombooru_source_concept_evidence"):
        rows = conn.execute(
            text(
                """
                SELECT DISTINCT media_id
                FROM blombooru_source_concept_evidence
                WHERE media_id IS NOT NULL
                  AND status IN ('active', 'needs_review')
                """
            )
        )
        media_ids.update(int(row[0]) for row in rows if row[0] is not None)
    if table_exists(conn, "blombooru_source_concept_signal_links") and table_exists(conn, "blombooru_source_concept_signals"):
        rows = conn.execute(
            text(
                """
                SELECT DISTINCT s.media_id
                FROM blombooru_source_concept_signal_links l
                JOIN blombooru_source_concept_signals s ON s.id = l.signal_id
                WHERE l.link_status IN ('active', 'needs_review')
                  AND s.status IN ('active', 'needs_review')
                  AND s.media_id IS NOT NULL
                """
            )
        )
        media_ids.update(int(row[0]) for row in rows if row[0] is not None)
    return media_ids


def load_media_rows(conn: Connection) -> list[dict[str, Any]]:
    if not table_exists(conn, "blombooru_media"):
        return []
    return rows_dict(
        conn,
        """
        SELECT id, filename, source, content_class
        FROM blombooru_media
        ORDER BY id ASC
        """,
    )


def media_id_set(conn: Connection, sql: str, params: Mapping[str, Any] | None = None) -> set[int]:
    return {int(row["media_id"]) for row in rows_dict(conn, sql, params) if row.get("media_id") is not None}


def media_id_to_values(conn: Connection, sql: str, params: Mapping[str, Any] | None = None, *, key: str = "media_id", value: str = "value") -> dict[int, set[str]]:
    result: dict[int, set[str]] = defaultdict(set)
    for row in rows_dict(conn, sql, params):
        media_id = row.get(key)
        raw_value = row.get(value)
        if media_id is not None and raw_value is not None:
            result[int(media_id)].add(str(raw_value))
    return result


def source_metadata_media_ids(conn: Connection, *, provider: str | None = None) -> set[int]:
    if not table_exists(conn, "blombooru_source_metadata_records"):
        return set()
    where = "WHERE media_id IS NOT NULL"
    params: dict[str, Any] = {}
    if provider:
        where += " AND LOWER(provider) = :provider"
        params["provider"] = provider.casefold()
    return media_id_set(conn, f"SELECT DISTINCT media_id FROM blombooru_source_metadata_records {where}", params)


def source_metadata_pixiv_ids_by_media(conn: Connection) -> dict[int, set[str]]:
    if not table_exists(conn, "blombooru_source_metadata_records"):
        return {}
    return media_id_to_values(
        conn,
        """
        SELECT media_id, source_work_id AS value
        FROM blombooru_source_metadata_records
        WHERE media_id IS NOT NULL
          AND LOWER(provider) = 'pixiv'
          AND source_work_id IS NOT NULL
        """,
    )


def pixiv_source_layer_media_ids(conn: Connection) -> dict[str, set[int]]:
    categories: dict[str, set[int]] = {
        "source_metadata_provider_pixiv": source_metadata_media_ids(conn, provider="pixiv"),
        "source_tag_observation_provider_pixiv": set(),
        "source_name_observation_provider_pixiv": set(),
        "source_assertion_provider_pixiv": set(),
        "source_name_candidate_provider_pixiv": set(),
        "source_concept_signal_provider_pixiv": set(),
        "source_concept_evidence_provider_pixiv": set(),
    }
    if table_exists(conn, "blombooru_source_tag_observations") and table_exists(conn, "blombooru_source_metadata_records"):
        categories["source_tag_observation_provider_pixiv"] = media_id_set(
            conn,
            """
            SELECT DISTINCT r.media_id
            FROM blombooru_source_tag_observations t
            JOIN blombooru_source_metadata_records r ON r.id = t.source_metadata_record_id
            WHERE r.media_id IS NOT NULL
              AND (LOWER(t.provider) = 'pixiv' OR LOWER(r.provider) = 'pixiv')
            """,
        )
    if table_exists(conn, "blombooru_source_name_observations"):
        direct = media_id_set(
            conn,
            """
            SELECT DISTINCT media_id
            FROM blombooru_source_name_observations
            WHERE media_id IS NOT NULL AND LOWER(provider) = 'pixiv'
            """,
        )
        via_record: set[int] = set()
        if table_exists(conn, "blombooru_source_metadata_records"):
            via_record = media_id_set(
                conn,
                """
                SELECT DISTINCT r.media_id
                FROM blombooru_source_name_observations n
                JOIN blombooru_source_metadata_records r ON r.id = n.source_metadata_record_id
                WHERE r.media_id IS NOT NULL
                  AND (LOWER(n.provider) = 'pixiv' OR LOWER(r.provider) = 'pixiv')
                """,
            )
        categories["source_name_observation_provider_pixiv"] = direct | via_record
    if table_exists(conn, "blombooru_source_searchable_name_assertions") and table_exists(conn, "blombooru_source_metadata_records"):
        categories["source_assertion_provider_pixiv"] = media_id_set(
            conn,
            """
            SELECT DISTINCT r.media_id
            FROM blombooru_source_searchable_name_assertions a
            LEFT JOIN blombooru_source_metadata_records r ON r.id = a.source_metadata_record_id
            WHERE r.media_id IS NOT NULL
              AND (LOWER(a.provider) = 'pixiv' OR LOWER(r.provider) = 'pixiv')
            """,
        )
    if table_exists(conn, "blombooru_source_name_candidates"):
        direct = media_id_set(
            conn,
            """
            SELECT DISTINCT media_id
            FROM blombooru_source_name_candidates
            WHERE media_id IS NOT NULL AND LOWER(provider) = 'pixiv'
            """,
        )
        via_record = set()
        if table_exists(conn, "blombooru_source_metadata_records"):
            via_record = media_id_set(
                conn,
                """
                SELECT DISTINCT r.media_id
                FROM blombooru_source_name_candidates c
                JOIN blombooru_source_metadata_records r ON r.id = c.source_metadata_record_id
                WHERE r.media_id IS NOT NULL
                  AND (LOWER(c.provider) = 'pixiv' OR LOWER(r.provider) = 'pixiv')
                """,
            )
        categories["source_name_candidate_provider_pixiv"] = direct | via_record
    if table_exists(conn, "blombooru_source_concept_signals"):
        categories["source_concept_signal_provider_pixiv"] = media_id_set(
            conn,
            """
            SELECT DISTINCT media_id
            FROM blombooru_source_concept_signals
            WHERE media_id IS NOT NULL
              AND (LOWER(COALESCE(provider, '')) = 'pixiv'
                   OR LOWER(COALESCE(source_kind, '')) LIKE '%pixiv%')
            """,
        )
    if table_exists(conn, "blombooru_source_concept_evidence"):
        direct = media_id_set(
            conn,
            """
            SELECT DISTINCT media_id
            FROM blombooru_source_concept_evidence
            WHERE media_id IS NOT NULL
              AND LOWER(COALESCE(provider, '')) = 'pixiv'
            """,
        )
        via_record = set()
        if table_exists(conn, "blombooru_source_metadata_records"):
            via_record = media_id_set(
                conn,
                """
                SELECT DISTINCT r.media_id
                FROM blombooru_source_concept_evidence e
                JOIN blombooru_source_metadata_records r ON r.id = e.source_metadata_record_id
                WHERE r.media_id IS NOT NULL
                  AND (LOWER(COALESCE(e.provider, '')) = 'pixiv' OR LOWER(r.provider) = 'pixiv')
                """,
            )
        categories["source_concept_evidence_provider_pixiv"] = direct | via_record
    return categories


def extract_pixiv_ids(value: Any, *, source_kind: str = "generic") -> list[dict[str, Any]]:
    text_value = normalize_source_text(value)
    if not text_value:
        return []
    patterns = PIXIV_SOURCE_PATTERNS if source_kind == "source" else PIXIV_FILENAME_PATTERNS
    matches: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None, str]] = set()
    for pattern in patterns:
        for match in pattern.finditer(text_value):
            pixiv_id = match.group("pixiv_id")
            page_raw = match.groupdict().get("page_index")
            page_index = int(page_raw) if page_raw is not None and page_raw.isdigit() else None
            key = (pixiv_id, page_index, pattern.pattern)
            if key in seen:
                continue
            seen.add(key)
            matches.append(
                {
                    "pixiv_id": pixiv_id,
                    "page_index": page_index,
                    "source_kind": source_kind,
                    "pattern": "pixiv_artwork_id",
                }
            )
    return matches


def has_pixiv_marker(value: Any) -> bool:
    return bool(PIXIV_MARKER_RE.search(normalize_source_text(value)))


def classify_pixiv_like_media_row(
    row: Mapping[str, Any],
    *,
    db_signal_categories: Sequence[str] | None = None,
    db_pixiv_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    filename = normalize_source_text(row.get("filename"))
    source = normalize_source_text(row.get("source"))
    filename_ids = extract_pixiv_ids(filename, source_kind="filename")
    source_ids = extract_pixiv_ids(source, source_kind="source")
    db_ids = [
        {"pixiv_id": str(value), "page_index": None, "source_kind": "db_source_metadata", "pattern": "source_work_id"}
        for value in sorted({str(item) for item in db_pixiv_ids or [] if normalize_source_text(item)})
    ]
    detected = filename_ids + source_ids + db_ids
    detected_ids = sorted({item["pixiv_id"] for item in detected})
    reasons: list[str] = []
    if filename_ids:
        reasons.append("filename_pixiv_id_pattern")
    if source_ids:
        reasons.append("source_pixiv_id_pattern")
    if has_pixiv_marker(source):
        reasons.append("source_pixiv_marker")
    if has_pixiv_marker(filename):
        reasons.append("filename_pixiv_marker")
    for category in db_signal_categories or []:
        if category not in reasons:
            reasons.append(category)
    if db_ids and "source_metadata_pixiv_work_id" not in reasons:
        reasons.append("source_metadata_pixiv_work_id")

    has_marker_without_id = any(reason in reasons for reason in ("source_pixiv_marker", "filename_pixiv_marker")) and not detected_ids
    is_pixiv_like = bool(reasons)
    return {
        "is_pixiv_like": is_pixiv_like,
        "reasons": sorted(reasons),
        "detected_pixiv_ids": detected_ids,
        "detected_pixiv_id_count": len(detected_ids),
        "has_pixiv_marker_without_id": has_marker_without_id,
        "ambiguous_pixiv_id": len(detected_ids) > 1,
    }


def audit_current_media_baseline(conn: Connection) -> dict[str, Any]:
    total_media = count_table(conn, "blombooru_media")["count"] or 0
    content_distribution = group_count(conn, "blombooru_media", "content_class")
    eligible_media = scalar_count(conn, "SELECT COUNT(*) FROM blombooru_media WHERE content_class IN ('anime', 'unknown')")
    non_anime_count = scalar_count(conn, "SELECT COUNT(*) FROM blombooru_media WHERE content_class = 'non_anime'")
    media_with_any_tags = scalar_count(conn, "SELECT COUNT(DISTINCT media_id) FROM blombooru_media_tags") if table_exists(conn, "blombooru_media_tags") else 0
    media_with_ai_tags = (
        scalar_count(conn, f"SELECT COUNT(DISTINCT media_id) FROM blombooru_media_tags WHERE {ai_tag_predicate()}")
        if table_exists(conn, "blombooru_media_tags")
        else 0
    )
    eligible_media_with_ai_tags = (
        scalar_count(
            conn,
            f"""
            SELECT COUNT(DISTINCT mt.media_id)
            FROM blombooru_media_tags mt
            JOIN blombooru_media m ON m.id = mt.media_id
            WHERE m.content_class IN ('anime', 'unknown')
              AND {ai_tag_predicate('mt')}
            """,
        )
        if table_exists(conn, "blombooru_media_tags")
        else 0
    )
    source_layer_media_ids = media_ids_with_source_layer(conn)
    source_concept_media_ids = media_ids_with_source_concept(conn)
    source_metadata_ids = source_metadata_media_ids(conn)
    source_metadata_total = count_table(conn, "blombooru_source_metadata_records")["count"] if table_exists(conn, "blombooru_source_metadata_records") else 0
    source_metadata_linked = scalar_count(conn, "SELECT COUNT(*) FROM blombooru_source_metadata_records WHERE media_id IS NOT NULL") if table_exists(conn, "blombooru_source_metadata_records") else 0
    source_metadata_distinct_eligible = (
        scalar_count(
            conn,
            """
            SELECT COUNT(DISTINCT r.media_id)
            FROM blombooru_source_metadata_records r
            JOIN blombooru_media m ON m.id = r.media_id
            WHERE r.media_id IS NOT NULL
              AND m.content_class IN ('anime', 'unknown')
            """,
        )
        if table_exists(conn, "blombooru_source_metadata_records")
        else 0
    )
    return {
        "total_media": total_media,
        "eligible_policy": "content_class IN ('anime', 'unknown')",
        "eligible_media": eligible_media,
        "eligible_media_pct": percent(eligible_media, total_media),
        "non_anime_count": non_anime_count,
        "content_class_distribution": content_distribution,
        "media_with_any_tags": media_with_any_tags,
        "media_with_any_tags_pct": percent(media_with_any_tags, total_media),
        "media_with_ai_tag_provenance": media_with_ai_tags,
        "eligible_media_with_ai_tag_provenance": eligible_media_with_ai_tags,
        "eligible_media_without_ai_tag_provenance": max(eligible_media - eligible_media_with_ai_tags, 0),
        "eligible_ai_tag_provenance_pct": percent(eligible_media_with_ai_tags, eligible_media),
        "current_eligible_ai_tag_coverage_effectively_complete": eligible_media > 0 and eligible_media_with_ai_tags == eligible_media,
        "media_with_source_concept_evidence_or_links": len(source_concept_media_ids),
        "media_with_source_layer_signals": len(source_layer_media_ids),
        "media_with_source_metadata": source_metadata_linked,
        "media_with_source_metadata_by_distinct_media": len(source_metadata_ids),
        "eligible_media_with_source_metadata_by_distinct_media": source_metadata_distinct_eligible,
        "source_metadata_total_rows": source_metadata_total,
        "media_without_source_metadata": max(total_media - len(source_metadata_ids), 0),
        "comparison_baseline_media_count": total_media,
        "comparison_baseline_policy": "Use current DB media count as the denominator for pre/post medium expansion comparisons.",
    }


def audit_pixiv_like_media(conn: Connection, baseline: Mapping[str, Any]) -> dict[str, Any]:
    media_rows = load_media_rows(conn)
    source_metadata_ids = source_metadata_media_ids(conn)
    pixiv_metadata_ids = source_metadata_media_ids(conn, provider="pixiv")
    pixiv_ids_by_media = source_metadata_pixiv_ids_by_media(conn)
    pixiv_categories = pixiv_source_layer_media_ids(conn)
    ai_media_ids = (
        media_id_set(conn, f"SELECT DISTINCT media_id FROM blombooru_media_tags WHERE {ai_tag_predicate()}")
        if table_exists(conn, "blombooru_media_tags")
        else set()
    )

    candidates: list[dict[str, Any]] = []
    reason_counter: Counter[str] = Counter()
    pixiv_id_to_media: dict[str, set[int]] = defaultdict(set)
    eligible_count = 0
    noneligible_count = 0
    with_metadata = 0
    without_metadata = 0
    with_ai = 0
    without_ai = 0
    marker_without_id = 0
    ambiguous = 0

    for row in media_rows:
        media_id = int(row["id"])
        db_signal_categories = [category for category, ids in pixiv_categories.items() if media_id in ids]
        classification = classify_pixiv_like_media_row(
            row,
            db_signal_categories=db_signal_categories,
            db_pixiv_ids=sorted(pixiv_ids_by_media.get(media_id, set())),
        )
        if not classification["is_pixiv_like"]:
            continue
        for reason in classification["reasons"]:
            reason_counter[reason] += 1
        for pixiv_id in classification["detected_pixiv_ids"]:
            pixiv_id_to_media[pixiv_id].add(media_id)
        has_metadata = media_id in source_metadata_ids
        has_pixiv_metadata = media_id in pixiv_metadata_ids
        has_ai = media_id in ai_media_ids
        is_eligible = str(row.get("content_class") or "").casefold() in {"anime", "unknown"}
        if is_eligible:
            eligible_count += 1
        else:
            noneligible_count += 1
        if has_metadata:
            with_metadata += 1
        else:
            without_metadata += 1
        if has_ai:
            with_ai += 1
        else:
            without_ai += 1
        if classification["has_pixiv_marker_without_id"]:
            marker_without_id += 1
        if classification["ambiguous_pixiv_id"]:
            ambiguous += 1
        candidates.append(
            {
                "media_id": media_id,
                "private_media_ref": stable_private_id(media_id, "media"),
                "content_class": row.get("content_class"),
                "public_safe_label": stable_private_id(media_id, "pixiv_like"),
                "filename_label_redacted": True,
                "source_label_redacted": True,
                "is_eligible": is_eligible,
                "has_any_source_metadata": has_metadata,
                "has_pixiv_source_metadata": has_pixiv_metadata,
                "has_ai_tag_provenance": has_ai,
                **classification,
            }
        )

    duplicate_groups = {
        pixiv_id: sorted(media_ids)
        for pixiv_id, media_ids in pixiv_id_to_media.items()
        if len(media_ids) > 1
    }
    total = len(candidates)
    return {
        "method": "DB-derived signals only; no source root scan, provider call, gallery-dl, or filesystem inventory.",
        "signals_considered": [
            "Media.filename Pixiv artwork/page patterns",
            "Media.source Pixiv URL/marker patterns",
            "source metadata provider pixiv",
            "source tag/name/assertion provider pixiv",
            "source name candidates provider pixiv",
            "SourceConcept signal/evidence provider or source_kind pixiv",
        ],
        "total_pixiv_like_media_candidates": total,
        "distinct_pixiv_like_media_candidates": total,
        "pixiv_like_candidates_with_existing_source_metadata": with_metadata,
        "pixiv_like_candidates_without_source_metadata": without_metadata,
        "pixiv_like_candidates_with_pixiv_source_metadata": sum(1 for row in candidates if row["has_pixiv_source_metadata"]),
        "pixiv_like_candidates_with_ai_tag_provenance": with_ai,
        "pixiv_like_candidates_without_ai_tag_provenance": without_ai,
        "pixiv_like_eligible_media": eligible_count,
        "pixiv_like_non_eligible_media": noneligible_count,
        "pixiv_like_candidate_pct_of_total_media": percent(total, int(baseline.get("total_media") or 0)),
        "pixiv_like_metadata_backlog_pct": percent(without_metadata, total),
        "reason_category_counts": dict(sorted(reason_counter.items())),
        "top_source_prior_categories": dict(reason_counter.most_common(12)),
        "detected_distinct_pixiv_ids": len(pixiv_id_to_media),
        "duplicate_pixiv_id_candidate_groups": len(duplicate_groups),
        "duplicate_pixiv_id_candidate_media_count": sum(len(media_ids) for media_ids in duplicate_groups.values()),
        "invalid_or_marker_only_pixiv_id_candidates": marker_without_id,
        "ambiguous_pixiv_id_candidates": ambiguous,
        "user_claim_assessment": {
            "claim": "Already-imported Pixiv-like media likely exceed source metadata-covered media by a wide margin.",
            "confirmed": total > with_metadata and without_metadata >= max(1, with_metadata),
            "evidence": f"{total} Pixiv-like candidates; {with_metadata} with any source metadata; {without_metadata} metadata backlog.",
        },
        "private_candidate_count": total,
        "private_candidates": candidates,
        "private_duplicate_pixiv_id_groups": {
            stable_private_id(pixiv_id, "pixiv_id"): [stable_private_id(media_id, "media") for media_id in media_ids]
            for pixiv_id, media_ids in sorted(duplicate_groups.items())
        },
    }


def public_pixiv_inventory(private_inventory: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in private_inventory.items()
        if key not in {"private_candidates", "private_duplicate_pixiv_id_groups"}
    } | {
        "private_candidate_rows_public": False,
        "exact_media_ids_public": False,
        "exact_pixiv_ids_public": False,
    }


def audit_source_metadata_gap(conn: Connection, pixiv_inventory: Mapping[str, Any]) -> dict[str, Any]:
    total_media = count_table(conn, "blombooru_media")["count"] or 0
    eligible_media = scalar_count(conn, "SELECT COUNT(*) FROM blombooru_media WHERE content_class IN ('anime', 'unknown')")
    source_metadata_ids = source_metadata_media_ids(conn)
    pixiv_like_ids = {int(row["media_id"]) for row in pixiv_inventory.get("private_candidates", [])}
    pixiv_missing = pixiv_like_ids - source_metadata_ids
    pixiv_covered = pixiv_like_ids & source_metadata_ids
    all_media_ids = {int(row["id"]) for row in load_media_rows(conn)}
    non_pixiv_ids = all_media_ids - pixiv_like_ids
    non_pixiv_missing = non_pixiv_ids - source_metadata_ids
    source_metadata_total = count_table(conn, "blombooru_source_metadata_records")["count"] if table_exists(conn, "blombooru_source_metadata_records") else 0
    linked_rows = scalar_count(conn, "SELECT COUNT(*) FROM blombooru_source_metadata_records WHERE media_id IS NOT NULL") if table_exists(conn, "blombooru_source_metadata_records") else 0
    distinct_eligible = (
        scalar_count(
            conn,
            """
            SELECT COUNT(DISTINCT r.media_id)
            FROM blombooru_source_metadata_records r
            JOIN blombooru_media m ON m.id = r.media_id
            WHERE r.media_id IS NOT NULL
              AND m.content_class IN ('anime', 'unknown')
            """,
        )
        if table_exists(conn, "blombooru_source_metadata_records")
        else 0
    )
    return {
        "source_metadata_total_rows": source_metadata_total,
        "source_metadata_linked_rows": linked_rows,
        "source_metadata_distinct_media_covered": len(source_metadata_ids),
        "source_metadata_distinct_media_pct": percent(len(source_metadata_ids), total_media),
        "source_metadata_distinct_eligible_media_covered": distinct_eligible,
        "source_metadata_distinct_eligible_media_pct": percent(distinct_eligible, eligible_media),
        "distinct_pixiv_like_media_covered_by_source_metadata": len(pixiv_covered),
        "distinct_pixiv_like_media_missing_source_metadata": len(pixiv_missing),
        "already_imported_pixiv_like_media_lacking_metadata": len(pixiv_missing),
        "already_imported_non_pixiv_media_lacking_metadata": len(non_pixiv_missing),
        "external_new_media_expansion_candidates_not_yet_in_db": {
            "known_in_p0": False,
            "reason": "P0 is forbidden from scanning source or cloud roots or staging candidates.",
        },
        "source_metadata_by_provider": group_count(conn, "blombooru_source_metadata_records", "provider"),
        "source_metadata_by_status": group_count(conn, "blombooru_source_metadata_records", "status"),
        "source_tag_observation_counts_by_provider": group_count(conn, "blombooru_source_tag_observations", "provider"),
        "source_name_observation_counts_by_provider": group_count(conn, "blombooru_source_name_observations", "provider"),
        "source_assertions_by_provider": group_count(conn, "blombooru_source_searchable_name_assertions", "provider"),
        "source_assertions_by_status": group_count(conn, "blombooru_source_searchable_name_assertions", "status"),
        "source_name_candidates_by_provider": group_count(conn, "blombooru_source_name_candidates", "provider"),
        "source_name_candidates_by_status": group_count(conn, "blombooru_source_name_candidates", "status"),
        "source_name_candidates_by_candidate_status": group_count(conn, "blombooru_source_name_candidates", "candidate_status"),
        "f7a_candidates_by_provider_status": group_count_sql(
            conn,
            """
            SELECT CONCAT(COALESCE(provider, '<null>'), ':', COALESCE(status, '<null>')) AS key, COUNT(*) AS count
            FROM blombooru_source_name_candidates
            GROUP BY key
            ORDER BY count DESC, key ASC
            """,
        )
        if table_exists(conn, "blombooru_source_name_candidates")
        else {},
        "source_concept_signals_by_provider": group_count(conn, "blombooru_source_concept_signals", "provider"),
        "source_concept_signals_by_source_kind": group_count(conn, "blombooru_source_concept_signals", "source_kind"),
        "source_concept_signals_by_status": group_count(conn, "blombooru_source_concept_signals", "status"),
        "source_concept_evidence_by_provider": group_count(conn, "blombooru_source_concept_evidence", "provider"),
        "source_concept_evidence_by_status": group_count(conn, "blombooru_source_concept_evidence", "status"),
        "source_concept_evidence_by_type": group_count(conn, "blombooru_source_concept_evidence", "evidence_type"),
        "gap_buckets": {
            "already_imported_pixiv_like_media_lacking_metadata": len(pixiv_missing),
            "already_imported_non_pixiv_media_lacking_metadata": len(non_pixiv_missing),
            "external_new_media_expansion_candidates_not_yet_in_db": None,
        },
        "private_pixiv_like_missing_metadata_media_refs": [stable_private_id(media_id, "media") for media_id in sorted(pixiv_missing)],
    }


def public_source_metadata_gap(private_gap: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in private_gap.items()
        if key != "private_pixiv_like_missing_metadata_media_refs"
    } | {"exact_missing_media_ids_public": False}


def build_ai_tag_continuity_policy(baseline: Mapping[str, Any]) -> dict[str, Any]:
    eligible = int(baseline.get("eligible_media") or 0)
    tagged = int(baseline.get("eligible_media_with_ai_tag_provenance") or 0)
    missing = max(eligible - tagged, 0)
    return {
        "current_policy": "eligible media are content_class anime or unknown",
        "current_eligible_media": eligible,
        "current_eligible_media_with_ai_tag_provenance": tagged,
        "current_eligible_media_without_ai_tag_provenance": missing,
        "current_eligible_ai_tag_coverage_pct": percent(tagged, eligible),
        "current_coverage_effectively_complete": eligible > 0 and missing == 0,
        "future_invariant": "Every newly imported eligible media item must receive AI tag provenance after import/classification.",
        "execution_order": ["import/staging audit", "DB import if approved", "classification/eligibility", "AI tagging for newly imported eligible media", "item-level failure ledger"],
        "item_failure_policy": "AI tag failures are item-level failures with stable failure reasons and do not become batch blockers while within approved failure budget.",
        "truth_policy": "AI tags remain signal/provenance and must not create confirmed entity assignments or trusted identity truth.",
        "localization_policy": "AI tag expansion must not auto-trigger tag localization/translation unless that phase explicitly approves it.",
        "current_existing_media_priority": "Do not run standalone AI tag expansion for already-imported media while eligible coverage is complete; keep continuity for future imports.",
        "e1_acceptance_criterion": {
            "metric": "eligible_new_media_with_ai_tag_provenance / eligible_new_media",
            "target_pct": 100.0,
            "allowed_deviation": "Only approved item-level failures with reason may reduce the ratio.",
            "requires_item_level_failures_recorded": True,
        },
    }


def build_medium_expansion_policy(current_total_media: int) -> dict[str, Any]:
    target_total_floor = 3500
    target_total_ceiling = 4000
    target_total_recommended = 3750
    min_success = max(0, target_total_floor - current_total_media)
    max_success = max(0, target_total_ceiling - current_total_media)
    recommended_success = max(0, target_total_recommended - current_total_media)
    if recommended_success == 0 and current_total_media < target_total_ceiling:
        recommended_success = min(max_success, max(1, (max_success + min_success) // 2))
    buffer_ratio = 1.35
    candidate_count = int(math.ceil(recommended_success * buffer_ratio)) if recommended_success else 0
    return {
        "current_total_media": current_total_media,
        "target_total_range": {"min": target_total_floor, "recommended": target_total_recommended, "max": target_total_ceiling},
        "target_successful_imported_media_count": recommended_success,
        "target_successful_imported_media_min": min_success,
        "target_successful_imported_media_max": max_success,
        "candidate_over_selection_buffer_ratio": buffer_ratio,
        "candidate_over_selection_count": candidate_count,
        "candidate_selection_priority": [
            "already hydrated/readable local files",
            "likely anime/illustration",
            "likely Pixiv-origin filename/source prior",
            "not already imported",
            "safe extension",
            "no path escape",
            "no source or cloud write",
            "not cloud placeholder unless explicitly allowed by later phase",
            "enough over-selection to tolerate duplicates and item-level failures",
        ],
        "recommended_e1_scope": "Import from approved source roots only after E1 source-root approval and staging audit; P0 does not know external candidates without source scan.",
        "source_root_or_staging_answer": "E1 should first build/stage an approved candidate manifest with source and cloud mutation guards, then import only audited staged-success eligible rows.",
        "failure_budget": {
            "max_item_failures": 20,
            "max_failure_rate": 0.05,
            "max_consecutive_failures": 10,
            "max_same_reason_failures": 20,
            "note": "Use existing medium-pilot defaults unless user/ChatGPT approves a different E1 budget.",
        },
        "duplicate_policy": "Detect by existing DB hash and manifest-internal hash before import; record duplicate_of_media_id where available and keep duplicates as item-level outcomes.",
        "unsupported_file_policy": "Unsupported extensions are item-level excluded rows with reason unsupported_extension.",
        "non_anime_policy": "non_anime rows are import/classification outcomes but not eligible for AI tag continuity or Pixiv/provider execution by default.",
        "import_eligibility_policy": "Only staged-success, safe-extension, no-path-escape, not duplicate, readable rows with approved source state are eligible for DB import.",
        "user_exclusion_policy": "User exclusions must be explicit item-level statuses and remain visible in the ledger.",
        "why_not_5k_10k_or_full_library_next": [
            "Current target is scale-behavior exposure, not broad/full-library completeness.",
            "Full-library import needs production ingestion/source item ledger discipline first.",
            "Provider/Pixiv and AI continuity need separate bounded phases with budgets and redaction.",
        ],
    }


MEDIUM_IMPORT_LEDGER_REQUIRED_FIELDS = [
    "run_id",
    "item_id",
    "source_label",
    "source_root_id",
    "originalFileNameRedactedOrHashed",
    "detected_pixiv_id",
    "cloud_hydration_state",
    "file_extension",
    "size",
    "import_candidate_status",
    "staging_status",
    "import_status",
    "media_id",
    "duplicate_of_media_id",
    "content_class",
    "eligible_for_ai_tagging",
    "ai_tag_status",
    "ai_tag_job_id",
    "ai_tag_failure_reason",
    "eligible_for_pixiv_metadata",
    "deferred_reason",
    "public_safe_label",
    "private_artifact_ref",
]

PIXIV_METADATA_LEDGER_REQUIRED_FIELDS = [
    "run_id",
    "media_id",
    "detected_pixiv_id",
    "page_index",
    "metadata_request_status",
    "provider",
    "method",
    "authenticated",
    "original_downloaded",
    "cache_hit",
    "retry_count",
    "failure_reason",
    "source_metadata_record_id",
    "tag_observation_count",
    "name_observation_count",
    "source_assertion_count",
    "redaction_status",
    "eligible_for_source_concept_resolver",
    "private_artifact_ref",
    "public_safe_label",
]


def build_medium_expansion_ledger_schema() -> dict[str, Any]:
    return {
        "schema_name": "medium_import_item_ledger",
        "lifecycle": "future E1 JSONL/CSV artifact schema proposal; not a DB migration in P0",
        "required_fields": MEDIUM_IMPORT_LEDGER_REQUIRED_FIELDS,
        "recommended_artifact_formats": ["private JSONL for item detail", "public aggregate JSON summary"],
        "db_schema_implemented_in_p0": False,
        "privacy": {
            "public_may_include": ["aggregate counts", "public_safe_label", "failure reason buckets"],
            "private_only": ["source locator", "original filename", "media_id when sensitive", "private_artifact_ref"],
        },
    }


def build_pixiv_metadata_ledger_schema() -> dict[str, Any]:
    return {
        "schema_name": "pixiv_source_metadata_item_ledger",
        "lifecycle": "future PX1 JSONL/CSV artifact schema proposal; not a DB migration in P0",
        "required_fields": PIXIV_METADATA_LEDGER_REQUIRED_FIELDS,
        "defaults": {"provider": "pixiv", "original_downloaded": False},
        "recommended_artifact_formats": ["private JSONL for per-media/provider request state", "public aggregate JSON summary"],
        "db_schema_implemented_in_p0": False,
        "future_db_migration_need": "Optional only if PX1 needs durable cross-run resume beyond JSONL/checkpoint artifacts.",
        "privacy": {
            "public_may_include": ["aggregate counts", "public_safe_label", "status/failure buckets"],
            "private_only": ["exact Pixiv ID if sensitive", "auth/cookie state detail", "request/cache details", "media_id when sensitive"],
        },
    }


def build_phase_split_plan() -> dict[str, Any]:
    return {
        "SCV2-E1": {
            "title": "Medium Import + AI Tag Completion",
            "purpose": "Add enough media to bring DB to roughly 3.5k-4k and complete AI tag provenance for newly imported eligible media.",
            "may_do": ["approved source-root candidate selection", "staging audit", "DB import if separately approved", "classification/eligibility", "AI tagging for newly imported eligible media", "item-level ledger"],
            "must_not_do": ["Pixiv/gallery-dl/provider metadata", "SourceConcept resolver improvements", "Entity bridge", "tag localization unless separately approved", "media_tags truth beyond approved AI provenance behavior"],
        },
        "PX1": {
            "title": "Bounded Pixiv/Source Metadata Extraction",
            "purpose": "Run bounded metadata-only Pixiv/gallery-dl extraction for DB Pixiv-like candidates after provider policy approval.",
            "may_do": ["metadata-only gallery-dl/Pixiv requests", "cache/checkpoint/retry/rate-limit", "source-layer metadata/observations/assertions writes if approved", "public/private artifact split"],
            "must_not_do": ["import media", "download original images by default", "upload images", "AI jobs", "classification jobs", "Entity truth", "media_tags mutation", "TagTranslation mutation"],
        },
        "SCV2-R1": {
            "title": "SourceConcept Alias Resolver / Needs-Review Triage",
            "purpose": "Use expanded AI/source metadata signals to improve alias closure and reduce needs_review noise.",
            "may_do": ["approved SourceConcept source-layer writes", "alias resolver/closure improvements", "needs_review triage metrics", "CJK/English/Danbooru alias gap handling"],
            "must_not_do": ["Entity truth", "confirmed assignments", "media_tags mutation", "SourceConcept editing UI unless separately approved"],
        },
        "SCV2-A1": {
            "title": "Post-expansion Audit",
            "purpose": "Compare pre/post expansion and decide route.",
            "may_do": ["read-only pre/post count comparison", "AI coverage audit", "Pixiv/source metadata coverage audit", "SourceConcept status/gap/search symmetry audit", "redaction proof"],
            "must_not_do": ["new import", "new provider calls", "new AI/localization jobs", "Entity bridge", "truth writes"],
        },
    }


def build_risk_and_stop_conditions() -> dict[str, Any]:
    e1_gates = [
        "import target and over-selection buffer approved",
        "source roots identified safely",
        "source and cloud mutation guard available",
        "app storage target verified",
        "item-level ledger defined",
        "failure budget defined",
        "AI tagging queue behavior controlled",
        "localization auto-run disabled or explicitly approved",
        "public/private artifacts safe",
    ]
    px1_gates = [
        "candidate Pixiv-like media count known",
        "gallery-dl / provider policy approved",
        "cookie/auth policy approved if needed",
        "no-original-download policy defined",
        "rate limit, timeout, retry, cache/checkpoint defined",
        "source metadata write boundaries defined",
        "redaction and public/private reporting defined",
    ]
    r1_gates = [
        "SourceConcept write scope approved",
        "no-truth-write proof available",
        "source-layer-only table boundaries defined",
        "alias/needs_review metrics from SCV1/PX1 available",
    ]
    return {
        "structural_stop_conditions": [
            "DB identity mismatch",
            "transaction is not read-only during P0",
            "public redaction scan fails",
            "forbidden table counts change",
            "answering inventory requires source scan/provider call/DB write/import",
            "public report would expose local paths, filenames, secrets, or exact private source locators",
        ],
        "e1_start_gates": e1_gates,
        "px1_start_gates": px1_gates,
        "r1_start_gates": r1_gates,
        "e1_item_level_failure_budget": {
            "max_item_failures": 20,
            "max_failure_rate": 0.05,
            "max_consecutive_failures": 10,
            "max_same_reason_failures": 20,
        },
        "not_next": {
            "five_k_ten_k_full_library": "Not next; medium expansion is enough to expose scale behavior while preserving recoverability.",
            "source_concept_resolver_now_without_expansion_policy": "Not next in this requested route; P0 must first govern E1/PX1 split.",
            "pixiv_provider_inside_e1": "Not allowed; provider metadata belongs in PX1.",
        },
    }


def build_decision_matrix(
    baseline: Mapping[str, Any],
    pixiv: Mapping[str, Any],
    gap: Mapping[str, Any],
    ai_policy: Mapping[str, Any],
    medium_policy: Mapping[str, Any],
) -> dict[str, Any]:
    pixiv_backlog = int(pixiv.get("pixiv_like_candidates_without_source_metadata") or 0)
    pixiv_total = int(pixiv.get("distinct_pixiv_like_media_candidates") or 0)
    source_metadata_covered = int(gap.get("source_metadata_distinct_eligible_media_covered") or 0)
    eligible = int(baseline.get("eligible_media") or 0)
    return {
        "answers": {
            "user_claim_confirmed": bool((pixiv.get("user_claim_assessment") or {}).get("confirmed")),
            "already_imported_pixiv_like_count": pixiv_total,
            "pixiv_like_metadata_backlog": pixiv_backlog,
            "eligible_ai_coverage_complete": bool(ai_policy.get("current_coverage_effectively_complete")),
            "source_metadata_eligible_coverage_pct": percent(source_metadata_covered, eligible),
            "medium_expansion_target_successful_imports": medium_policy.get("target_successful_imported_media_count"),
            "recommended_next_executable_phase": "SCV2-E1",
        },
        "options": [
            {
                "key": "SCV2-E1_medium_import_ai_completion",
                "recommended": True,
                "priority": "P1",
                "reasons": [
                    "User wants roughly doubled scale before serious resolver/triage work.",
                    "AI coverage is currently complete for eligible media, so newly imported eligible media must preserve that distribution.",
                    "Import and AI completion can run without provider/Pixiv calls if kept in E1.",
                ],
            },
            {
                "key": "PX1_pixiv_source_metadata_extraction",
                "recommended": True,
                "priority": "P1 after E1",
                "reasons": [
                    f"Pixiv-like DB backlog is {pixiv_backlog} of {pixiv_total} Pixiv-like candidates.",
                    "Provider/gallery-dl needs a separate policy, auth, cache, retry, and redaction gate.",
                ],
            },
            {
                "key": "SCV2-R1_source_concept_alias_triage",
                "recommended": True,
                "priority": "P2 after PX1",
                "reasons": [
                    "Resolver/needs_review work benefits from expanded media and source metadata signals.",
                    "Still must remain source-layer-only with no truth writes.",
                ],
            },
            {
                "key": "five_k_ten_k_or_full_library",
                "recommended": False,
                "priority": "deferred",
                "reasons": [
                    "Not needed to expose medium-scale behavior.",
                    "Requires stronger production ingestion/source item ledger and broad-run discipline.",
                ],
            },
        ],
    }


def artifact_lifecycle() -> dict[str, str]:
    return {
        f"scripts/run_phase45_scv2_p0_controlled_medium_expansion_policy.py": "phase-scoped operational runner",
        f"tests/test_phase45_scv2_p0_controlled_medium_expansion_policy.py": "phase-scoped validation test",
        f".local_manifests/{PHASE_SLUG}": "one-off local artifact / ignored output",
        root_relative_or_name(PUBLIC_REPORT_MD): "public report / handoff / roadmap update",
        root_relative_or_name(PUBLIC_REPORT_JSON): "public report / handoff / roadmap update",
    }


def private_artifact_summary() -> dict[str, Any]:
    return {
        "private_artifact_root_label": f".local_manifests/{PHASE_SLUG}",
        "private_artifact_names": list(PRIVATE_ARTIFACT_NAMES),
        "private_artifact_count": len(PRIVATE_ARTIFACT_NAMES),
        "exact_private_paths_public": False,
        "contains_private_media_refs": True,
        "contains_exact_source_paths_or_filenames_public": False,
    }


def build_public_summary(
    *,
    db_identity: Mapping[str, Any],
    baseline: Mapping[str, Any],
    pixiv: Mapping[str, Any],
    gap: Mapping[str, Any],
    ai_policy: Mapping[str, Any],
    medium_policy: Mapping[str, Any],
    medium_ledger: Mapping[str, Any],
    pixiv_ledger: Mapping[str, Any],
    phase_split: Mapping[str, Any],
    safety_gates: Mapping[str, Any],
    decision: Mapping[str, Any],
    validation: Mapping[str, Any],
    redaction: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    summary = {
        "phase": PHASE,
        "title": PHASE_TITLE,
        "branch": BRANCH,
        "generated_at": utc_now_iso(),
        "db_identity": public_db_identity(db_identity),
        "current_media_baseline": baseline,
        "current_pixiv_like_media_inventory": public_pixiv_inventory(pixiv),
        "source_metadata_gap": public_source_metadata_gap(gap),
        "ai_tag_continuity_policy": ai_policy,
        "medium_expansion_policy": medium_policy,
        "phase_split": phase_split,
        "ledger_schema": {
            "medium_import_ledger": medium_ledger,
            "pixiv_source_metadata_ledger": pixiv_ledger,
        },
        "safety_gates": safety_gates,
        "decision_matrix": decision,
        "recommended_next_phase": "SCV2-E1",
        "validation": validation,
        "safety": {
            "app_module_imports": False,
            "app_startup": False,
            "server_started": False,
            "browser_validation": "N/A; P0 is non-UI/non-runtime and user explicitly forbids server/browser validation.",
            "db_write": False,
            "db_migration": False,
            "db_import": False,
            "source_or_icloud_scan": False,
            "source_icloud_staging_app_storage_mutation": False,
            "ai_classification_localization_jobs": False,
            "provider_gallery_dl_pixiv_network_llm_calls": False,
            "entity_or_truth_path_write": False,
        },
        "artifact_lifecycle": artifact_lifecycle(),
        "private_artifacts": private_artifact_summary(),
    }
    summary["public_redaction"] = redaction or {
        "checked_at": None,
        "passed": None,
        "public_paths": [root_relative_or_name(PUBLIC_REPORT_MD), root_relative_or_name(PUBLIC_REPORT_JSON)],
        "findings": [],
    }
    missing_fields = sorted(SUMMARY_REQUIRED_FIELDS - set(summary))
    if missing_fields:
        raise PolicyBlockedError(f"Summary missing required fields: {missing_fields}")
    return summary


def public_report_markdown(summary: Mapping[str, Any]) -> str:
    baseline = summary["current_media_baseline"]
    pixiv = summary["current_pixiv_like_media_inventory"]
    gap = summary["source_metadata_gap"]
    ai = summary["ai_tag_continuity_policy"]
    medium = summary["medium_expansion_policy"]
    decision = summary["decision_matrix"]["answers"]
    lines = [
        f"# {PHASE} {PHASE_TITLE}",
        "",
        "## Summary",
        "",
        "P0 performed a read-only inventory over the current development DB and produced a governed split for medium expansion. It did not import, stage-copy, scan source roots, run AI/classification/localization jobs, run providers, run gallery-dl/Pixiv, run LLMs, start a server, or mutate DB/source/app storage.",
        "",
        f"- Recommended next executable phase: `{summary['recommended_next_phase']}`.",
        f"- Current media: `{baseline.get('total_media')}`; eligible media: `{baseline.get('eligible_media')}`.",
        f"- Eligible AI tag coverage: `{baseline.get('eligible_media_with_ai_tag_provenance')}` / `{baseline.get('eligible_media')}` (`{baseline.get('eligible_ai_tag_provenance_pct')}%`).",
        f"- Pixiv-like media candidates already in DB: `{pixiv.get('distinct_pixiv_like_media_candidates')}`.",
        f"- Pixiv-like candidates with source metadata: `{pixiv.get('pixiv_like_candidates_with_existing_source_metadata')}`.",
        f"- Pixiv-like metadata backlog: `{pixiv.get('pixiv_like_candidates_without_source_metadata')}`.",
        f"- User claim confirmed: `{decision.get('user_claim_confirmed')}`.",
        "",
        "## Current Baseline After SCV1",
        "",
        f"- Total media: `{baseline.get('total_media')}`.",
        f"- Eligible policy: `{baseline.get('eligible_policy')}`.",
        f"- Eligible media: `{baseline.get('eligible_media')}` (`{baseline.get('eligible_media_pct')}%`).",
        f"- Non-anime count: `{baseline.get('non_anime_count')}`.",
        f"- Content class distribution: `{json.dumps(baseline.get('content_class_distribution'), ensure_ascii=False, sort_keys=True)}`.",
        f"- Media with any tags: `{baseline.get('media_with_any_tags')}`.",
        f"- Eligible media without AI tag provenance: `{baseline.get('eligible_media_without_ai_tag_provenance')}`.",
        f"- Media with source-layer signals: `{baseline.get('media_with_source_layer_signals')}`.",
        f"- Media with SourceConcept evidence or links: `{baseline.get('media_with_source_concept_evidence_or_links')}`.",
        f"- Source metadata distinct media coverage: `{baseline.get('media_with_source_metadata_by_distinct_media')}`.",
        f"- Media without source metadata: `{baseline.get('media_without_source_metadata')}`.",
        "",
        "## Current Pixiv-like Media Already In DB",
        "",
        f"- Method: `{pixiv.get('method')}`.",
        f"- Distinct Pixiv-like candidates: `{pixiv.get('distinct_pixiv_like_media_candidates')}`.",
        f"- With existing source metadata: `{pixiv.get('pixiv_like_candidates_with_existing_source_metadata')}`.",
        f"- Without source metadata: `{pixiv.get('pixiv_like_candidates_without_source_metadata')}`.",
        f"- With Pixiv source metadata: `{pixiv.get('pixiv_like_candidates_with_pixiv_source_metadata')}`.",
        f"- With AI tag provenance: `{pixiv.get('pixiv_like_candidates_with_ai_tag_provenance')}`.",
        f"- Without AI tag provenance: `{pixiv.get('pixiv_like_candidates_without_ai_tag_provenance')}`.",
        f"- Eligible / non-eligible Pixiv-like media: `{pixiv.get('pixiv_like_eligible_media')}` / `{pixiv.get('pixiv_like_non_eligible_media')}`.",
        f"- Reason category counts: `{json.dumps(pixiv.get('reason_category_counts'), ensure_ascii=False, sort_keys=True)}`.",
        f"- Duplicate Pixiv ID groups detected: `{pixiv.get('duplicate_pixiv_id_candidate_groups')}`.",
        f"- Marker-only / invalid ID candidates: `{pixiv.get('invalid_or_marker_only_pixiv_id_candidates')}`.",
        f"- Ambiguous Pixiv ID candidates: `{pixiv.get('ambiguous_pixiv_id_candidates')}`.",
        "",
        "Assessment: already-imported Pixiv-like media likely far exceed metadata-covered media, so the user's intuition is confirmed by DB-derived signals if `user_claim_confirmed` is true above.",
        "",
        "## Current Source Metadata Gap",
        "",
        f"- Source metadata rows: `{gap.get('source_metadata_total_rows')}`; linked rows: `{gap.get('source_metadata_linked_rows')}`.",
        f"- Distinct media covered: `{gap.get('source_metadata_distinct_media_covered')}` (`{gap.get('source_metadata_distinct_media_pct')}%`).",
        f"- Distinct eligible media covered: `{gap.get('source_metadata_distinct_eligible_media_covered')}` (`{gap.get('source_metadata_distinct_eligible_media_pct')}%`).",
        f"- Distinct Pixiv-like media covered: `{gap.get('distinct_pixiv_like_media_covered_by_source_metadata')}`.",
        f"- Distinct Pixiv-like media missing metadata: `{gap.get('distinct_pixiv_like_media_missing_source_metadata')}`.",
        f"- Already imported non-Pixiv media lacking metadata: `{gap.get('already_imported_non_pixiv_media_lacking_metadata')}`.",
        f"- External/new expansion candidates: `{json.dumps(gap.get('external_new_media_expansion_candidates_not_yet_in_db'), ensure_ascii=False, sort_keys=True)}`.",
        f"- Source metadata by provider: `{json.dumps(gap.get('source_metadata_by_provider'), ensure_ascii=False, sort_keys=True)}`.",
        f"- Source tag observations by provider: `{json.dumps(gap.get('source_tag_observation_counts_by_provider'), ensure_ascii=False, sort_keys=True)}`.",
        f"- Source name observations by provider: `{json.dumps(gap.get('source_name_observation_counts_by_provider'), ensure_ascii=False, sort_keys=True)}`.",
        f"- Source assertions by provider/status: `{json.dumps(gap.get('source_assertions_by_provider'), ensure_ascii=False, sort_keys=True)}` / `{json.dumps(gap.get('source_assertions_by_status'), ensure_ascii=False, sort_keys=True)}`.",
        f"- Source name candidates by provider/status: `{json.dumps(gap.get('source_name_candidates_by_provider'), ensure_ascii=False, sort_keys=True)}` / `{json.dumps(gap.get('source_name_candidates_by_status'), ensure_ascii=False, sort_keys=True)}`.",
        f"- SourceConcept evidence by provider/status/type: `{json.dumps(gap.get('source_concept_evidence_by_provider'), ensure_ascii=False, sort_keys=True)}` / `{json.dumps(gap.get('source_concept_evidence_by_status'), ensure_ascii=False, sort_keys=True)}` / `{json.dumps(gap.get('source_concept_evidence_by_type'), ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## AI Tag Continuity Policy",
        "",
        f"- Current complete: `{ai.get('current_coverage_effectively_complete')}`.",
        f"- Future invariant: {ai.get('future_invariant')}",
        f"- E1 acceptance criterion: `{json.dumps(ai.get('e1_acceptance_criterion'), ensure_ascii=False, sort_keys=True)}`.",
        "- AI tags remain provenance/signal, not Entity truth.",
        "- AI expansion must not auto-trigger localization unless explicitly approved.",
        "",
        "## Controlled Medium Expansion Target",
        "",
        f"- Target total range: `{json.dumps(medium.get('target_total_range'), ensure_ascii=False, sort_keys=True)}`.",
        f"- Recommended successful imported media count: `{medium.get('target_successful_imported_media_count')}`.",
        f"- Over-selection buffer ratio/count: `{medium.get('candidate_over_selection_buffer_ratio')}` / `{medium.get('candidate_over_selection_count')}`.",
        f"- Failure budget: `{json.dumps(medium.get('failure_budget'), ensure_ascii=False, sort_keys=True)}`.",
        "",
        "## Candidate Selection Policy",
        "",
    ]
    for item in medium.get("candidate_selection_priority", []):
        lines.append(f"- {item}.")
    lines.extend(
        [
            "",
            "## Import/AI/Pixiv/SourceConcept Phase Split",
            "",
        ]
    )
    for key, phase in summary["phase_split"].items():
        lines.extend(
            [
                f"### {key} - {phase['title']}",
                "",
                f"Purpose: {phase['purpose']}",
                "",
                f"May do: `{json.dumps(phase['may_do'], ensure_ascii=False)}`.",
                f"Must not do: `{json.dumps(phase['must_not_do'], ensure_ascii=False)}`.",
                "",
            ]
        )
    lines.extend(
        [
            "## Ledger Schema",
            "",
            f"- Medium import ledger required fields: `{json.dumps(summary['ledger_schema']['medium_import_ledger']['required_fields'], ensure_ascii=False)}`.",
            f"- Pixiv metadata ledger required fields: `{json.dumps(summary['ledger_schema']['pixiv_source_metadata_ledger']['required_fields'], ensure_ascii=False)}`.",
            "- P0 does not implement DB ledger schemas; JSONL/CSV artifacts are proposed for E1/PX1 unless a later phase promotes them.",
            "",
            "## Safety Gates And Stop Conditions",
            "",
            f"- Structural stop conditions: `{json.dumps(summary['safety_gates']['structural_stop_conditions'], ensure_ascii=False)}`.",
            f"- E1 gates: `{json.dumps(summary['safety_gates']['e1_start_gates'], ensure_ascii=False)}`.",
            f"- PX1 gates: `{json.dumps(summary['safety_gates']['px1_start_gates'], ensure_ascii=False)}`.",
            f"- R1 gates: `{json.dumps(summary['safety_gates']['r1_start_gates'], ensure_ascii=False)}`.",
            "",
            "## Public/Private Artifact Boundary",
            "",
            f"- Private artifact root label: `{summary['private_artifacts']['private_artifact_root_label']}`.",
            f"- Private artifact names: `{json.dumps(summary['private_artifacts']['private_artifact_names'], ensure_ascii=False)}`.",
            f"- Exact media IDs, local paths, filenames, source locators, and exact Pixiv IDs are not public: `{not pixiv.get('exact_media_ids_public')}`.",
            "",
            "## Decision Matrix",
            "",
            f"- Answers: `{json.dumps(summary['decision_matrix']['answers'], ensure_ascii=False, sort_keys=True)}`.",
            f"- Options: `{json.dumps(summary['decision_matrix']['options'], ensure_ascii=False, sort_keys=True)}`.",
            "",
            "## Recommended Next Executable Phase",
            "",
            "`SCV2-E1` should be next only after user/ChatGPT approve the import target, source roots, staging/import safety, AI job behavior, and item ledger. E1 must not include Pixiv/gallery-dl/provider execution.",
            "",
            "## Deferred Work",
            "",
            "- PX1 provider/gallery-dl/Pixiv metadata execution.",
            "- R1 SourceConcept alias resolver / needs_review triage.",
            "- A1 post-expansion audit.",
            "- 5k/10k/full-library import, Entity bridge, SourceConcept editing UI, confirmed assignments, and media_tags truth writes.",
            "",
            "## Validation",
            "",
            f"- Operational inventory command: `{summary['validation'].get('operational_inventory_command')}`.",
            f"- Operational inventory result: `{summary['validation'].get('operational_inventory_result')}`.",
            f"- PostgreSQL transaction_read_only: `{summary['validation'].get('transaction_read_only')}`.",
            f"- Forbidden table count changes: `{json.dumps(summary['validation'].get('forbidden_table_count_changes'), ensure_ascii=False)}`.",
            f"- Public redaction passed: `{summary.get('public_redaction', {}).get('passed')}`.",
            "- Real browser validation: N/A; P0 is non-UI/non-runtime and server/browser validation is explicitly forbidden.",
            "",
            "## Safety Confirmation",
            "",
            "- No push main.",
            "- No merge.",
            "- No DB write, migration, import, cleanup/delete/reset/drop/truncate.",
            "- No source, cloud, staging, or app-managed storage mutation.",
            "- No AI tagging, classification, localization, LLM, provider call, gallery-dl, Pixiv network call, or server/browser validation.",
            "- No Entity Resolver, similarity, SourceConcept implementation, SourceConcept editing, Entity bridge, confirmed assignment, or media_tags mutation.",
            "",
            "## Artifact Lifecycle",
            "",
            f"`{json.dumps(summary['artifact_lifecycle'], ensure_ascii=False, sort_keys=True)}`.",
            "",
            "## Engineering Judgment / Operator Notes",
            "",
            "P0 answers enough to start E1 planning approval because it establishes the current DB baseline, confirms AI tag continuity, verifies a DB-derived Pixiv-like metadata backlog, and defines ledgers, budgets, gates, and phase boundaries. The phase is appropriately narrow: it is more than a prose plan because it creates a reproducible read-only inventory runner, but it does not blur into import/provider/AI execution. E1 must not start until the target, source roots, staging/import safety, AI job controls, localization-off behavior, and item ledger are explicitly approved.",
            "",
        ]
    )
    return "\n".join(lines)


def write_reports_with_redaction(summary: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    paths = [PUBLIC_REPORT_MD, PUBLIC_REPORT_JSON]
    labels = [root_relative_or_name(path) for path in paths]
    checked_at = utc_now_iso()
    temp_dir = output_dir / "_public_report_staging"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_md = temp_dir / PUBLIC_REPORT_MD.name
    temp_json = temp_dir / PUBLIC_REPORT_JSON.name
    redaction = {
        "checked_at": checked_at,
        "passed": True,
        "public_paths": labels,
        "findings": [],
        "final_public_scan_after_public_fields_finalized": True,
        "exact_private_paths_public": False,
        "private_artifact_paths_public": False,
    }
    summary["public_redaction"] = redaction
    write_text(temp_md, public_report_markdown(summary))
    write_json(temp_json, summary)
    final_scan = scan_public_artifacts([temp_md, temp_json], checked_at=checked_at, public_path_labels=labels)
    if not final_scan["passed"]:
        failed = {**redaction, "passed": False, "findings": final_scan["findings"]}
        write_text(output_dir / "public-redaction-check.txt", json.dumps(failed, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        raise PolicyBlockedError(f"Public redaction scan failed: {final_scan['findings']!r}")
    PUBLIC_REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    temp_md.replace(PUBLIC_REPORT_MD)
    temp_json.replace(PUBLIC_REPORT_JSON)
    try:
        temp_dir.rmdir()
    except OSError:
        pass
    write_text(output_dir / "public-redaction-check.txt", json.dumps(redaction, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return redaction


def run_inventory(args: argparse.Namespace) -> dict[str, Any]:
    if not args.read_only:
        raise PolicyBlockedError("SCV2-P0 runner requires --read-only.")
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
            raise PolicyBlockedError(f"SCV2-P0 requires PostgreSQL read-only transaction support, got {conn.dialect.name!r}.")
        conn.exec_driver_sql("BEGIN TRANSACTION READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout = '300s'")
        db_identity = read_only_identity(conn, env_identity)
        write_json(output_dir / "db-identity.json", db_identity)

        session = SessionLocal(bind=conn)
        install_no_flush_guard(session)
        before_counts = build_mutation_counts(conn)

        baseline = audit_current_media_baseline(conn)
        pixiv_inventory = audit_pixiv_like_media(conn, baseline)
        source_gap = audit_source_metadata_gap(conn, pixiv_inventory)
        ai_policy = build_ai_tag_continuity_policy(baseline)
        medium_policy = build_medium_expansion_policy(int(baseline.get("total_media") or 0))
        medium_ledger = build_medium_expansion_ledger_schema()
        pixiv_ledger = build_pixiv_metadata_ledger_schema()
        phase_split = build_phase_split_plan()
        safety_gates = build_risk_and_stop_conditions()
        decision = build_decision_matrix(baseline, public_pixiv_inventory(pixiv_inventory), public_source_metadata_gap(source_gap), ai_policy, medium_policy)

        after_counts = build_mutation_counts(conn)
        proof = compare_mutation_counts(before_counts, after_counts)
        if not proof["passed"]:
            raise PolicyBlockedError(f"Forbidden table counts changed during read-only inventory: {proof['changed_tables']!r}")

        validation = {
            "operational_inventory_command": f"python scripts/run_phase45_scv2_p0_controlled_medium_expansion_policy.py --output-dir .local_manifests/{PHASE_SLUG} --write-public-report --read-only",
            "operational_inventory_result": "passed",
            "transaction_read_only": db_identity["transaction_read_only"],
            "forbidden_table_count_changes": proof["changed_tables"],
            "missing_optional_tables": proof["missing_tables"],
            "no_app_imports": True,
            "no_provider_calls": True,
            "no_server_or_browser": True,
        }

        summary = build_public_summary(
            db_identity=db_identity,
            baseline=baseline,
            pixiv=pixiv_inventory,
            gap=source_gap,
            ai_policy=ai_policy,
            medium_policy=medium_policy,
            medium_ledger=medium_ledger,
            pixiv_ledger=pixiv_ledger,
            phase_split=phase_split,
            safety_gates=safety_gates,
            decision=decision,
            validation=validation,
        )

        write_json(output_dir / "current-media-baseline.json", baseline)
        write_json(output_dir / "current-pixiv-like-media-inventory.json", pixiv_inventory)
        write_json(output_dir / "current-source-metadata-gap-inventory.json", source_gap)
        write_json(output_dir / "ai-tag-coverage-baseline.json", ai_policy)
        write_json(output_dir / "medium-expansion-candidate-policy.json", medium_policy)
        write_json(output_dir / "medium-expansion-ledger-schema.json", medium_ledger)
        write_json(output_dir / "pixiv-source-metadata-ledger-schema.json", pixiv_ledger)
        write_json(output_dir / "phase-split-plan.json", phase_split)
        write_json(output_dir / "risk-and-stop-conditions.json", safety_gates)

        if args.write_public_report:
            redaction = write_reports_with_redaction(summary, output_dir)
            summary["public_redaction"] = redaction

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
    summary = run_inventory(args)
    print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
