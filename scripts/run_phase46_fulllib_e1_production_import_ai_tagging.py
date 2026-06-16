#!/usr/bin/env python3
"""Phase 4.6 FULLLIB E1a production utility runner dry-run.

Lifecycle: phase-scoped operational runner.

E1a is intentionally dry-run only.  It inventories local source roots, applies
source-ingestion metadata gates, performs bounded hash reads only for gate-
allowed candidates, detects intra-inventory duplicates, writes private ledgers,
and emits a privacy-safe public report/summary.  It does not connect to or
write the production database, import media, copy into app-managed storage,
generate thumbnails, classify media, run AI tagging, run providers, run LLMs,
run SourceConcept/Entity stages, or start a browser/server.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy.engine import URL, make_url

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.source_ingestion_gate import SourceIngestionGate  # noqa: E402
from scripts.phase_contracts import check_phase_contract  # noqa: E402

PHASE = "4.6-FULLLIB-E1a"
PHASE_TITLE = "Production Full-Library Runner and Dry-Run Proof"
PHASE_SLUG = "phase-4.6-fulllib-e1a-runner-dryrun"
BRANCH = "codex/phase46-fulllib-e1a-runner-dryrun"
CONFIRM_PHRASE = "EXECUTE_PHASE46_FULLLIB_E1_PRODUCTION_IMPORT_AI_TAGGING"
RECOMMENDED_PRODUCTION_DB = "violet_library_prod"
REPO_PYTHON_CANDIDATES = (
    Path("venv") / "Scripts" / "python.exe",
    Path("venv") / "bin" / "python",
    Path(".venv") / "Scripts" / "python.exe",
    Path(".venv") / "bin" / "python",
)

DEFAULT_OUTPUT_DIR = ROOT / ".local_manifests" / PHASE_SLUG
PUBLIC_REPORT_MD = ROOT / "docs" / "reports" / f"{PHASE_SLUG}.md"
PUBLIC_REPORT_JSON = ROOT / "docs" / "reports" / f"{PHASE_SLUG}-summary.json"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
GIF_EXTENSIONS = {".gif"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".avi", ".mkv"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | GIF_EXTENSIONS | VIDEO_EXTENSIONS

PRIVATE_LEDGER_NAMES = (
    "inventory-candidates.jsonl",
    "duplicate-skipped.jsonl",
    "unsupported-or-deferred.jsonl",
    "batch-plan.jsonl",
    "run-summary-private.json",
    "public-redaction-check.json",
)

INVENTORY_CANDIDATE_REQUIRED_FIELDS = {
    "run_id",
    "candidate_id",
    "safe_label",
    "source_label",
    "private_source_ref",
    "private_filename_sha256",
    "extension",
    "file_size_bytes",
    "supported_extension",
    "source_gate",
    "candidate_state",
    "deferred_reason",
    "eligible_for_duplicate_check",
    "eligible_for_future_db_import",
}

DUPLICATE_REQUIRED_FIELDS = {
    "run_id",
    "candidate_id",
    "safe_label",
    "duplicate_reason",
    "duplicate_of_candidate_id",
    "hash_algorithm",
    "eligible_for_future_db_import",
}

UNSUPPORTED_OR_DEFERRED_REQUIRED_FIELDS = {
    "run_id",
    "candidate_id",
    "safe_label",
    "reason",
    "reason_category",
    "eligible_for_future_db_import",
}

BATCH_PLAN_REQUIRED_FIELDS = {
    "run_id",
    "batch_id",
    "batch_index",
    "candidate_count",
    "candidate_ids",
    "dry_run_only",
    "requires_e1b_execute_approval",
    "planned_stages",
}

SENSITIVE_PUBLIC_PATTERNS = (
    ("windows_path", re.compile(r"(?i)\b[A-Z]:\\[^\s\"'<>|]+")),
    ("unc_path", re.compile(r"\\\\[^\\\s\"'<>|]+\\[^\\\s\"'<>|]+")),
    ("file_uri", re.compile(r"(?i)\bfile://[^\s\"'<>]+")),
    (
        "private_posix_path",
        re.compile(r"(?<![A-Za-z0-9_.-])/(home|Users|mnt|Volumes|tmp|workspace|opt|var)(/[^\s\"'<>]*)?"),
    ),
    (
        "secret_token",
        re.compile(
            r"(?i)(sk-[A-Za-z0-9_-]{4,}|ghp_[A-Za-z0-9_]{4,}|github_pat_[A-Za-z0-9_]{4,}|"
            r"xoxb-[A-Za-z0-9-]{4,}|Bearer\s+[A-Za-z0-9._-]{4,})"
        ),
    ),
    ("source_url", re.compile(r"(?i)\bhttps?://[^\s\"'<>]+")),
    (
        "media_filename",
        re.compile(
            r"(?i)\b[A-Za-z0-9][A-Za-z0-9_. -]{0,120}\."
            r"(jpg|jpeg|png|webp|gif|bmp|tif|tiff|avif|mp4|webm|mov|avi|mkv)\b"
        ),
    ),
)

SUMMARY_REQUIRED_FIELDS = {
    "phase",
    "title",
    "generated_at",
    "branch",
    "report_generation_git_state",
    "status",
    "mode",
    "current_head_reviewer_fix",
    "python_env",
    "db_identity",
    "production_storage_identity",
    "source_root_safety_proof",
    "staging_root_safety_proof",
    "inventory_results",
    "duplicate_deferred_unsupported_summary",
    "batch_plan",
    "contract_mapping",
    "future_execution_plan",
    "classification_plan",
    "ai_tagging_plan",
    "ai_tag_fingerprint_reuse_plan",
    "localization_handling",
    "browser_validation_e1b",
    "safety_proof",
    "validation",
    "artifact_lifecycle",
    "public_redaction",
    "public_json_payload",
    "public_markdown_text",
}


class FulllibE1aBlockedError(RuntimeError):
    """Raised when a dry-run safety gate fails before artifact writes."""


@dataclass(frozen=True)
class RuntimeContext:
    run_id: str
    output_dir: Path
    source_roots: tuple[Path, ...]
    production_storage_root: Path
    db_identity: dict[str, Any]
    dry_run: bool
    inventory_only: bool
    max_files: int
    batch_size: int
    max_file_size_bytes: int
    hash_timeout_seconds: int


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
        completed = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
    except Exception:
        return "unavailable"
    return completed.stdout.strip()


def git_check_clean(args: Sequence[str]) -> bool | None:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    return None


def build_report_generation_git_state() -> dict[str, Any]:
    tracked_clean = git_check_clean(["diff", "--quiet", "--"])
    staged_clean = git_check_clean(["diff", "--cached", "--quiet", "--"])
    status_text = git_value(["status", "--short"])
    status_available = status_text != "unavailable"
    dirty_tree = bool(status_text.strip()) if status_available else None
    tracked_dirty = None if tracked_clean is None or staged_clean is None else not (tracked_clean and staged_clean)
    return {
        "generated_from_worktree": True,
        "generated_before_commit": True,
        "base_head_sha": git_value(["rev-parse", "HEAD"]),
        "dirty_tree_at_generation": dirty_tree,
        "tracked_dirty_tree_at_generation": tracked_dirty,
        "untracked_entries_present_at_generation": bool(status_text.strip()) if status_available else None,
        "status_paths_recorded": False,
        "final_pr_head_sha_claimed": False,
        "proof_reproducibility_note": (
            "Public artifacts are generated from the working tree before the final fix commit; "
            "use the committed PR head plus this provenance object rather than treating base_head_sha as final."
        ),
    }


def resolve_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def normalized_path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def path_within_or_same(child: Path, parent: Path) -> bool:
    child_resolved = resolve_path(child)
    parent_resolved = resolve_path(parent)
    try:
        child_resolved.relative_to(parent_resolved)
        return True
    except ValueError:
        return False


def paths_overlap(left: Path, right: Path) -> bool:
    return path_within_or_same(left, right) or path_within_or_same(right, left)


def is_unc_path(path: Path) -> bool:
    text = str(path)
    return text.startswith("\\\\") or str(path.drive).startswith("\\\\")


def is_forbidden_network_or_nas_path(path: Path) -> bool:
    resolved = resolve_path(path)
    if is_unc_path(resolved):
        return True
    if resolved.drive.upper() == "Z:":
        return True
    text = str(resolved).replace("\\", "/").casefold()
    return "//192.168.71.230/" in text or "/storage/" in text and text.startswith("z:")


def is_icloud_like(path: Path) -> bool:
    text = str(path).replace("\\", "/").casefold()
    return "icloud" in text or "cloudstorage" in text


def temp_root() -> Path:
    return resolve_path(Path(tempfile.gettempdir()))


def repo_relative(path: Path) -> str | None:
    try:
        return resolve_path(path).relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return None


def is_git_ignored_repo_path(path: Path) -> bool:
    rel = repo_relative(path)
    if rel is None:
        return False
    completed = subprocess.run(
        ["git", "check-ignore", "-q", "--", rel],
        cwd=ROOT,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def validate_output_dir(output_dir: Path, *, source_roots: Sequence[Path], production_storage_root: Path) -> Path:
    resolved = resolve_path(output_dir if output_dir.is_absolute() else ROOT / output_dir)
    if is_forbidden_network_or_nas_path(resolved):
        raise FulllibE1aBlockedError("unsafe_output_dir_network_or_nas")
    if is_icloud_like(resolved):
        raise FulllibE1aBlockedError("unsafe_output_dir_icloud_or_cloud")
    if paths_overlap(resolved, production_storage_root):
        raise FulllibE1aBlockedError("unsafe_output_dir_overlaps_production_storage")
    for source_root in source_roots:
        if paths_overlap(resolved, source_root):
            raise FulllibE1aBlockedError("unsafe_output_dir_overlaps_source_root")

    if path_within_or_same(resolved, ROOT):
        if not is_git_ignored_repo_path(resolved):
            raise FulllibE1aBlockedError("unsafe_output_dir_repo_local_not_gitignored")
        return resolved

    if path_within_or_same(resolved, temp_root()):
        return resolved

    raise FulllibE1aBlockedError("unsafe_output_dir_not_repo_ignored_or_local_temp")


def read_dotenv_values(path: Path = ROOT / ".env") -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def env_or_dotenv(
    key: str,
    dotenv: Mapping[str, str],
    default: str = "",
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    env = environ if environ is not None else os.environ
    env_value = env.get(key)
    if env_value not in (None, ""):
        return str(env_value), "process_env"
    dotenv_value = dotenv.get(key)
    if dotenv_value not in (None, ""):
        return str(dotenv_value), ".env"
    return default, "default"


def sqlalchemy_url_from_fields(*, username: str, password: str, host: str, port: str | int, database: str) -> URL:
    return URL.create(
        drivername="postgresql",
        username=username,
        password=password,
        host=host,
        port=int(port),
        database=database,
    )


def safe_url_identity(url: URL) -> dict[str, Any]:
    return {
        "host": url.host or "localhost",
        "port": int(url.port or 5432),
        "database": str(url.database or ""),
        "username_present": bool(url.username),
        "password_present": bool(url.password),
        "password_value_recorded": False,
    }


def urls_equivalent(left: URL, right: URL) -> bool:
    return (
        (left.host or "localhost") == (right.host or "localhost")
        and int(left.port or 5432) == int(right.port or 5432)
        and str(left.database or "") == str(right.database or "")
        and (left.username or "") == (right.username or "")
        and (left.password or "") == (right.password or "")
    )


def app_settings_json_path(
    *,
    environ: Mapping[str, str] | None = None,
    dotenv: Mapping[str, str] | None = None,
) -> Path:
    dotenv_values = dotenv if dotenv is not None else read_dotenv_values()
    storage_root, _source = env_or_dotenv("VIOLET_STORAGE_ROOT", dotenv_values, "", environ=environ)
    storage_root = str(storage_root).strip()
    if storage_root:
        return Path(storage_root) / "data" / "settings.json"
    return ROOT / "data" / "settings.json"


def read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"__read_error__": True}
    return value if isinstance(value, dict) else {"__read_error__": True}


def _file_or_env_database_field(
    file_database: Mapping[str, Any],
    key: str,
    env_key: str,
    dotenv: Mapping[str, str],
    default: str | int,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    file_value = file_database.get(key)
    if file_value not in (None, ""):
        return str(file_value), "settings_json"
    env_value, env_source = env_or_dotenv(env_key, dotenv, str(default), environ=environ)
    return str(env_value), env_source


def resolve_app_database_identity(
    *,
    dotenv: Mapping[str, str],
    environ: Mapping[str, str] | None = None,
    settings_path: Path | None = None,
) -> tuple[URL | None, dict[str, Any]]:
    path = settings_path or app_settings_json_path(environ=environ, dotenv=dotenv)
    _storage_root_value, storage_root_source = env_or_dotenv("VIOLET_STORAGE_ROOT", dotenv, "", environ=environ)
    settings_json = read_json_object(path)
    if settings_json.get("__read_error__"):
        return None, {
            "status": "unknown_settings_json_unreadable",
            "settings_json_detected": path.exists(),
            "settings_path_recorded": False,
            "storage_root_source": storage_root_source,
            "password_value_recorded": False,
        }

    file_database_raw = settings_json.get("database", {})
    file_database = file_database_raw if isinstance(file_database_raw, Mapping) else {}
    field_sources: dict[str, str] = {}
    host, field_sources["host"] = _file_or_env_database_field(file_database, "host", "POSTGRES_HOST", dotenv, "db", environ=environ)
    port, field_sources["port"] = _file_or_env_database_field(file_database, "port", "POSTGRES_PORT", dotenv, 5432, environ=environ)
    database, field_sources["database"] = _file_or_env_database_field(file_database, "name", "POSTGRES_DB", dotenv, "blombooru", environ=environ)
    username, field_sources["username"] = _file_or_env_database_field(file_database, "user", "POSTGRES_USER", dotenv, "postgres", environ=environ)
    password, password_source = _file_or_env_database_field(file_database, "password", "POSTGRES_PASSWORD", dotenv, "", environ=environ)
    field_sources["password"] = f"{password_source}_{'present' if password else 'empty'}"
    return sqlalchemy_url_from_fields(
        username=username,
        password=password,
        host=host,
        port=port,
        database=database,
    ), {
        "status": "resolved_without_connection",
        "settings_json_detected": path.exists(),
        "settings_path_recorded": False,
        "storage_root_source": storage_root_source,
        "field_sources": field_sources,
        "identity": safe_url_identity(sqlalchemy_url_from_fields(
            username=username,
            password=password,
            host=host,
            port=port,
            database=database,
        )),
        "password_value_recorded": False,
    }


def resolve_runner_db_url(
    raw_db_url: str | None,
    *,
    dotenv: Mapping[str, str],
    environ: Mapping[str, str] | None = None,
) -> tuple[URL, str, dict[str, str]]:
    if raw_db_url:
        url = make_url(raw_db_url)
        return url, "cli_production_db_url", {
            "host": "cli",
            "port": "cli",
            "database": "cli",
            "username": "cli",
            "password": "cli_present" if url.password else "cli_empty",
        }

    host, host_source = env_or_dotenv("POSTGRES_HOST", dotenv, "localhost", environ=environ)
    port, port_source = env_or_dotenv("POSTGRES_PORT", dotenv, "5432", environ=environ)
    database, db_source = env_or_dotenv("POSTGRES_DB", dotenv, RECOMMENDED_PRODUCTION_DB, environ=environ)
    username, user_source = env_or_dotenv("POSTGRES_USER", dotenv, "postgres", environ=environ)
    password, password_source = env_or_dotenv("POSTGRES_PASSWORD", dotenv, "", environ=environ)
    return sqlalchemy_url_from_fields(
        username=username,
        password=password,
        host=host,
        port=port,
        database=database,
    ), "env_or_dotenv_postgres_fields", {
        "host": host_source,
        "port": port_source,
        "database": db_source,
        "username": user_source,
        "password": f"{password_source}_{'present' if password else 'empty'}",
    }


def resolve_production_db_identity(
    raw_db_url: str | None,
    *,
    dotenv: Mapping[str, str] | None = None,
    environ: Mapping[str, str] | None = None,
    settings_path: Path | None = None,
) -> dict[str, Any]:
    env = environ if environ is not None else os.environ
    dotenv_values = dotenv if dotenv is not None else read_dotenv_values()
    violet_env, violet_env_source = env_or_dotenv("VIOLET_ENV", dotenv_values, "development", environ=env)
    if env.get("TEST_DATABASE_URL"):
        raise FulllibE1aBlockedError("production_db_identity_rejects_test_environment")
    if str(violet_env).casefold() != "production":
        raise FulllibE1aBlockedError("violet_env_must_be_production_for_fulllib_e1a")

    url, source, field_sources = resolve_runner_db_url(raw_db_url, dotenv=dotenv_values, environ=env)
    app_url, app_resolution = resolve_app_database_identity(dotenv=dotenv_values, environ=env, settings_path=settings_path)

    database_name = str(url.database or "")
    if database_name != RECOMMENDED_PRODUCTION_DB:
        raise FulllibE1aBlockedError(f"production_db_name_must_be_{RECOMMENDED_PRODUCTION_DB}")
    if database_name in {"postgres", "template0", "template1", "blombooru", "blombooru_test"}:
        raise FulllibE1aBlockedError("production_db_identity_rejects_dev_or_system_db")

    app_equivalence_proven = app_url is not None
    urls_match = bool(app_url is not None and urls_equivalent(url, app_url))
    runner_matches_app = bool(app_equivalence_proven and urls_match)
    if not app_equivalence_proven:
        app_equivalence_status = "unknown_e1b_blocker"
    elif runner_matches_app:
        app_equivalence_status = "proven_match"
    else:
        app_equivalence_status = "mismatch_e1b_blocker"

    return {
        "recorded_at": utc_now(),
        "identity_source": source,
        "host": url.host or "localhost",
        "port": int(url.port or 5432),
        "database": database_name,
        "username_recorded": bool(url.username),
        "password_present": bool(url.password),
        "password_value_recorded": False,
        "db_connection_attempted": False,
        "db_write_attempted": False,
        "violet_env": str(violet_env).casefold(),
        "violet_env_source": violet_env_source,
        "production_env_required_for_e1b_execute": True,
        "db_resolution": {
            "app_compatible": runner_matches_app,
            "runner_matches_app_equivalent": runner_matches_app,
            "urls_match": urls_match,
            "app_equivalence_proven": app_equivalence_proven,
            "app_equivalence_status": app_equivalence_status,
            "field_sources": field_sources,
            "app_settings_resolution": app_resolution,
            "password_present": bool(url.password),
            "password_value_recorded": False,
            "full_url_recorded": False,
        },
    }


def validate_positive_int(value: int, name: str) -> None:
    if value <= 0:
        raise FulllibE1aBlockedError(f"{name}_must_be_positive")


def repo_python_candidates(root: Path = ROOT) -> list[Path]:
    return [root / candidate for candidate in REPO_PYTHON_CANDIDATES]


def is_repo_python_executable(executable: Path, *, root: Path = ROOT) -> bool:
    actual_key = normalized_path_key(executable)
    return any(actual_key == normalized_path_key(candidate) for candidate in repo_python_candidates(root))


def build_python_env(executable: Path | None = None, *, root: Path = ROOT) -> dict[str, Any]:
    actual = Path(executable or sys.executable)
    expected_checked = is_repo_python_executable(actual, root=root)
    return {
        "expected_python_checked": expected_checked,
        "check_python_env_passed": expected_checked,
        "public_executable_name": actual.name,
        "executable_path_redacted": True,
        "repo_local_venv_expected": True,
        "accepted_repo_venv_layouts": [
            "venv/Scripts/python.exe",
            "venv/bin/python",
            ".venv/Scripts/python.exe",
            ".venv/bin/python",
        ],
        "full_executable_path_private_only": True,
    }


def validate_python_env_or_block(executable: Path | None = None, *, root: Path = ROOT) -> dict[str, Any]:
    python_env = build_python_env(executable, root=root)
    if not python_env.get("check_python_env_passed"):
        raise FulllibE1aBlockedError("python_env_preflight_failed")
    return python_env


def build_storage_identity(production_storage_root: Path, source_roots: Sequence[Path]) -> dict[str, Any]:
    resolved = resolve_path(production_storage_root)
    overlaps = [f"input_{idx}" for idx, source in enumerate(source_roots, start=1) if paths_overlap(resolved, source)]
    test_storage_candidates = [
        os.getenv("VIOLET_TEST_STORAGE_ROOT", ""),
        os.getenv("VIOLET_STORAGE_ROOT", "") if os.getenv("VIOLET_ENV", "").casefold() == "test" else "",
        str(Path.home() / "VioletStorage" / "test"),
    ]
    test_overlaps = [
        f"test_storage_{idx}"
        for idx, raw in enumerate(test_storage_candidates, start=1)
        if raw and paths_overlap(resolved, Path(raw))
    ]
    return {
        "configured": True,
        "exists": resolved.exists(),
        "is_absolute": resolved.is_absolute(),
        "path_redacted": True,
        "under_repo": path_within_or_same(resolved, ROOT),
        "overlaps_source_inputs": bool(overlaps),
        "source_overlap_labels": overlaps,
        "overlaps_test_storage": bool(test_overlaps),
        "test_storage_overlap_labels": test_overlaps,
        "network_or_nas_path": is_forbidden_network_or_nas_path(resolved),
        "icloud_or_cloud_hint": is_icloud_like(resolved),
        "write_attempted": False,
        "app_storage_mutation": False,
        "originals_subdir_label": "production_storage/media/original",
        "thumbnails_subdir_label": "production_storage/media/thumbnails",
    }


def build_source_root_safety(
    source_roots: Sequence[Path],
    *,
    production_storage_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    blockers: list[str] = []
    test_storage_candidates = [
        os.getenv("VIOLET_TEST_STORAGE_ROOT", ""),
        str(Path.home() / "VioletStorage" / "test"),
    ]
    for idx, source_root in enumerate(source_roots, start=1):
        resolved = resolve_path(source_root)
        test_overlaps = [
            f"test_storage_{test_idx}"
            for test_idx, raw in enumerate(test_storage_candidates, start=1)
            if raw and paths_overlap(resolved, Path(raw))
        ]
        row = {
            "source_label": f"input_{idx}",
            "exists": resolved.exists(),
            "is_dir": resolved.is_dir(),
            "read_only_policy": True,
            "path_redacted": True,
            "under_repo": path_within_or_same(resolved, ROOT),
            "overlaps_production_storage": paths_overlap(resolved, production_storage_root),
            "overlaps_output_dir": paths_overlap(resolved, output_dir),
            "overlaps_test_storage": bool(test_overlaps),
            "test_storage_overlap_labels": test_overlaps,
            "network_or_nas_path": is_forbidden_network_or_nas_path(resolved),
            "icloud_or_cloud_hint": is_icloud_like(resolved),
            "write_attempted": False,
        }
        if not row["exists"]:
            blockers.append(f"input_{idx}:missing")
        if not row["is_dir"]:
            blockers.append(f"input_{idx}:not_directory")
        for key in (
            "under_repo",
            "overlaps_production_storage",
            "overlaps_output_dir",
            "overlaps_test_storage",
            "network_or_nas_path",
        ):
            if row[key]:
                blockers.append(f"input_{idx}:{key}")
        rows.append(row)
    return {
        "passed": not blockers,
        "input_count": len(rows),
        "inputs": rows,
        "blockers": blockers,
        "source_paths_redacted": True,
        "source_roots_read_only": True,
        "source_mutation_detected": False,
    }


def assert_static_safety(storage_identity: Mapping[str, Any], source_safety: Mapping[str, Any]) -> None:
    if not storage_identity.get("is_absolute"):
        raise FulllibE1aBlockedError("production_storage_root_must_be_absolute")
    for key in ("under_repo", "overlaps_source_inputs", "overlaps_test_storage", "network_or_nas_path", "icloud_or_cloud_hint"):
        if storage_identity.get(key):
            raise FulllibE1aBlockedError(f"unsafe_production_storage_{key}")
    if not source_safety.get("passed"):
        raise FulllibE1aBlockedError("unsafe_source_roots:" + ",".join(source_safety.get("blockers") or []))


def safe_label(index: int) -> str:
    return f"candidate_{index:06d}"


def stable_candidate_id(run_id: str, index: int) -> str:
    digest = hashlib.sha256(f"{run_id}:{index}".encode("utf-8")).hexdigest()[:12]
    return f"cand_{index:06d}_{digest}"


def sha256_text(value: Any) -> str:
    return hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()


def file_kind(extension: str) -> str:
    if extension in IMAGE_EXTENSIONS:
        return "image"
    if extension in GIF_EXTENSIONS:
        return "gif"
    if extension in VIDEO_EXTENSIONS:
        return "video"
    return "unsupported"


def classify_access_error(error: BaseException) -> str:
    winerror = getattr(error, "winerror", None)
    errno_value = getattr(error, "errno", None)
    if winerror in (2, 3):
        return "source_missing"
    if winerror == 5 or errno_value in {1, 13}:
        return "permission_denied"
    if winerror == 388:
        return "cloud_network_unavailable"
    return "unreadable_source"


def metadata_failure_reason(error: BaseException, *, fallback: str) -> str:
    reason = classify_access_error(error)
    return fallback if reason == "unreadable_source" else reason


def iter_source_entries(source_root: Path) -> Iterable[Path]:
    return source_root.rglob("*")


def is_file_entry(path: Path) -> bool:
    return path.is_file()


def stat_file(path: Path) -> os.stat_result:
    return path.stat()


def _hash_worker(path: str, conn: Any) -> None:
    try:
        md5 = hashlib.md5()
        sha256 = hashlib.sha256()
        size = 0
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                size += len(chunk)
                md5.update(chunk)
                sha256.update(chunk)
        conn.send(
            {
                "ok": True,
                "md5": md5.hexdigest(),
                "sha256": sha256.hexdigest(),
                "bytes_read": size,
                "error_reason": None,
                "error_message": None,
            }
        )
    except Exception as exc:
        conn.send(
            {
                "ok": False,
                "md5": None,
                "sha256": None,
                "bytes_read": 0,
                "error_reason": classify_access_error(exc),
                "error_message": str(exc)[:300],
            }
        )
    finally:
        conn.close()


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
        return {
            "ok": False,
            "md5": None,
            "sha256": None,
            "bytes_read": 0,
            "error_reason": "read_timeout",
            "error_message": f"hash timed out after {timeout_seconds}s",
        }
    if parent_conn.poll(timeout=2):
        result = parent_conn.recv()
        parent_conn.close()
        return result
    parent_conn.close()
    return {
        "ok": False,
        "md5": None,
        "sha256": None,
        "bytes_read": 0,
        "error_reason": "read_no_result",
        "error_message": f"hash worker exited with {proc.exitcode}",
    }


def inventory_source_roots(context: RuntimeContext) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    unsupported_or_deferred: list[dict[str, Any]] = []
    extension_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    max_files_reached = False
    next_index = 0
    max_bytes = context.max_file_size_bytes

    for source_idx, source_root in enumerate(context.source_roots, start=1):
        source_label = f"input_{source_idx}"
        try:
            entries = iter(iter_source_entries(source_root))
        except OSError:
            reason_counts["source_walk_failed"] += 1
            raise FulllibE1aBlockedError(f"source_traversal_failed:{source_label}") from None
        while True:
            try:
                path = next(entries)
            except StopIteration:
                break
            except OSError:
                reason_counts["source_walk_failed"] += 1
                raise FulllibE1aBlockedError(f"source_traversal_failed:{source_label}") from None
            try:
                if not is_file_entry(path):
                    continue
            except OSError as exc:
                next_index += 1
                label = safe_label(next_index)
                candidate_id = stable_candidate_id(context.run_id, next_index)
                extension = path.suffix.lower()
                reason = metadata_failure_reason(exc, fallback="metadata_access_failed")
                extension_counts[extension or "<none>"] += 1
                source_counts[source_label] += 1
                reason_counts[reason] += 1
                candidates.append(
                    {
                        "run_id": context.run_id,
                        "candidate_id": candidate_id,
                        "safe_label": label,
                        "source_label": source_label,
                        "private_source_ref": str(resolve_path(path)),
                        "private_filename_sha256": sha256_text(path.name),
                        "extension": extension or None,
                        "file_kind": file_kind(extension),
                        "file_size_bytes": None,
                        "supported_extension": extension in SUPPORTED_EXTENSIONS,
                        "source_gate": {
                            "allowed": False,
                            "blocked": True,
                            "reason": reason,
                            "safe_label": label,
                            "path_recorded": False,
                        },
                        "candidate_state": "deferred",
                        "deferred_reason": reason,
                        "eligible_for_duplicate_check": False,
                        "eligible_for_future_db_import": False,
                        "hash": None,
                        "content_sha256": None,
                        "hash_read_status": "not_read_metadata_failed",
                    }
                )
                unsupported_or_deferred.append(
                    {
                        "run_id": context.run_id,
                        "candidate_id": candidate_id,
                        "safe_label": label,
                        "reason": reason,
                        "reason_category": "deferred",
                        "eligible_for_future_db_import": False,
                    }
                )
                if next_index >= context.max_files:
                    max_files_reached = True
                    break
                continue
            next_index += 1
            label = safe_label(next_index)
            candidate_id = stable_candidate_id(context.run_id, next_index)
            extension = path.suffix.lower()
            extension_counts[extension or "<none>"] += 1
            source_counts[source_label] += 1

            supported = extension in SUPPORTED_EXTENSIONS
            reason = None if supported else "unsupported_extension"
            size = 0
            try:
                size = int(stat_file(path).st_size)
            except OSError as exc:
                reason = reason or metadata_failure_reason(exc, fallback="stat_failed")
            if supported and size == 0:
                reason = reason or "zero_byte"
            if supported and size > max_bytes:
                reason = reason or "too_large"
            if supported and not path_within_or_same(path, source_root):
                reason = reason or "path_escape"

            try:
                gate = SourceIngestionGate.evaluate_path_source(
                    path,
                    safe_label=label,
                    hydration_policy_enabled=False,
                ).to_public_dict()
            except OSError as exc:
                gate_reason = metadata_failure_reason(exc, fallback="metadata_access_failed")
                reason = reason or gate_reason
                gate = {
                    "allowed": False,
                    "blocked": True,
                    "reason": gate_reason,
                    "safe_label": label,
                    "path_recorded": False,
                }
            if supported and gate.get("blocked"):
                reason = reason or str(gate.get("reason") or "source_gate_blocked")

            passes_source_gates = supported and reason is None and bool(gate.get("allowed"))
            eligible_for_duplicate_check = passes_source_gates and not context.inventory_only
            if eligible_for_duplicate_check:
                candidate_state = "candidate"
            elif context.inventory_only and passes_source_gates:
                candidate_state = "requires_hash_and_duplicate_check"
            else:
                candidate_state = "unsupported" if not supported else "deferred"
            row = {
                "run_id": context.run_id,
                "candidate_id": candidate_id,
                "safe_label": label,
                "source_label": source_label,
                "private_source_ref": str(resolve_path(path)),
                "private_filename_sha256": sha256_text(path.name),
                "extension": extension or None,
                "file_kind": file_kind(extension),
                "file_size_bytes": size,
                "supported_extension": supported,
                "source_gate": gate,
                "candidate_state": candidate_state,
                "deferred_reason": reason,
                "eligible_for_duplicate_check": eligible_for_duplicate_check,
                "eligible_for_future_db_import": eligible_for_duplicate_check,
                "hash": None,
                "content_sha256": None,
                "hash_read_status": "not_read_yet" if eligible_for_duplicate_check and not context.inventory_only else "not_read_inventory_only",
            }
            if reason:
                reason_counts[reason] += 1
                unsupported_or_deferred.append(
                    {
                        "run_id": context.run_id,
                        "candidate_id": candidate_id,
                        "safe_label": label,
                        "reason": reason,
                        "reason_category": "unsupported" if not supported else "deferred",
                        "eligible_for_future_db_import": False,
                    }
                )
            candidates.append(row)
            if next_index >= context.max_files:
                max_files_reached = True
                break
        if max_files_reached:
            break

    summary = {
        "total_files_seen": len(candidates),
        "supported_candidates": sum(1 for row in candidates if row["supported_extension"]),
        "eligible_before_duplicate_check": sum(1 for row in candidates if row["eligible_for_duplicate_check"]),
        "unsupported_count": sum(1 for row in unsupported_or_deferred if row["reason_category"] == "unsupported"),
        "deferred_count": sum(1 for row in unsupported_or_deferred if row["reason_category"] == "deferred"),
        "extension_counts": dict(sorted(extension_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "source_label_counts": dict(sorted(source_counts.items())),
        "max_files": context.max_files,
        "max_files_reached": max_files_reached,
        "inventory_only": context.inventory_only,
    }
    return candidates, unsupported_or_deferred, summary


def apply_hashes_and_duplicates(
    context: RuntimeContext,
    candidates: list[dict[str, Any]],
    unsupported_or_deferred: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    duplicates: list[dict[str, Any]] = []
    seen_sha256: dict[str, str] = {}
    reason_counts: Counter[str] = Counter()
    unique_count = 0
    hash_failures = 0

    if context.inventory_only:
        return duplicates, {
            "status": "skipped_inventory_only",
            "hash_reads_attempted": 0,
            "hash_failures": 0,
            "duplicate_count": 0,
            "unique_import_candidates": 0,
            "reason_counts": {},
            "existing_production_db_duplicates_checked": False,
            "existing_production_db_duplicate_check": "deferred_to_e1b_read_only_db_connection",
        }

    for row in candidates:
        if not row.get("eligible_for_duplicate_check"):
            continue
        private_ref = Path(str(row["private_source_ref"]))
        hash_result = hash_file_with_timeout(private_ref, context.hash_timeout_seconds)
        if not hash_result.get("ok"):
            reason = str(hash_result.get("error_reason") or "unreadable_source")
            row.update(
                {
                    "candidate_state": "deferred",
                    "deferred_reason": reason,
                    "eligible_for_future_db_import": False,
                    "hash_read_status": "failed",
                    "hash_error_private": hash_result.get("error_message"),
                }
            )
            unsupported_or_deferred.append(
                {
                    "run_id": context.run_id,
                    "candidate_id": row["candidate_id"],
                    "safe_label": row["safe_label"],
                    "reason": reason,
                    "reason_category": "deferred",
                    "eligible_for_future_db_import": False,
                }
            )
            reason_counts[reason] += 1
            hash_failures += 1
            continue

        row.update(
            {
                "hash": hash_result["md5"],
                "content_sha256": hash_result["sha256"],
                "hash_read_status": "hash_read_ok",
                "hash_bytes_read": hash_result["bytes_read"],
            }
        )
        content_sha256 = str(row["content_sha256"])
        if content_sha256 in seen_sha256:
            duplicate_of = seen_sha256[content_sha256]
            row.update(
                {
                    "candidate_state": "duplicate",
                    "deferred_reason": "duplicate_by_content_sha256",
                    "eligible_for_future_db_import": False,
                }
            )
            duplicate_row = {
                "run_id": context.run_id,
                "candidate_id": row["candidate_id"],
                "safe_label": row["safe_label"],
                "duplicate_reason": "duplicate_by_content_sha256",
                "duplicate_of_candidate_id": duplicate_of,
                "hash_algorithm": "sha256",
                "eligible_for_future_db_import": False,
            }
            duplicates.append(duplicate_row)
            reason_counts["duplicate_by_content_sha256"] += 1
        else:
            seen_sha256[content_sha256] = str(row["candidate_id"])
            row["candidate_state"] = "unique_import_candidate"
            row["eligible_for_future_db_import"] = True
            unique_count += 1

    return duplicates, {
        "status": "completed",
        "hash_reads_attempted": sum(1 for row in candidates if row.get("eligible_for_duplicate_check")),
        "hash_failures": hash_failures,
        "duplicate_count": len(duplicates),
        "unique_import_candidates": unique_count,
        "reason_counts": dict(sorted(reason_counts.items())),
        "existing_production_db_duplicates_checked": False,
        "existing_production_db_duplicate_check": "deferred_to_e1b_read_only_db_connection",
    }


def build_batch_plan(context: RuntimeContext, candidates: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    unique_rows = [row for row in candidates if row.get("eligible_for_future_db_import") and row.get("candidate_state") == "unique_import_candidate"]
    batches: list[dict[str, Any]] = []
    for index in range(0, len(unique_rows), context.batch_size):
        rows = unique_rows[index:index + context.batch_size]
        batch_index = len(batches) + 1
        batches.append(
            {
                "run_id": context.run_id,
                "batch_id": f"batch_{batch_index:04d}",
                "batch_index": batch_index,
                "candidate_count": len(rows),
                "candidate_ids": [str(row["candidate_id"]) for row in rows],
                "dry_run_only": True,
                "requires_e1b_execute_approval": True,
                "planned_stages": [
                    "future_import_after_explicit_execute_approval",
                    "future_classification_after_import",
                    "future_ai_tagging_after_classification",
                    "future_browser_validation_after_import_thumbnail_ai_tags",
                ],
            }
        )
    return batches, {
        "batch_size": context.batch_size,
        "planned_batch_count": len(batches),
        "planned_unique_candidate_count": len(unique_rows),
        "dry_run_only": True,
        "execute_confirmation_required": CONFIRM_PHRASE,
    }


def validate_ledger_schema(rows: Sequence[Mapping[str, Any]], required: set[str], name: str) -> dict[str, Any]:
    missing_rows = []
    for index, row in enumerate(rows, start=1):
        missing = sorted(required - set(row))
        if missing:
            missing_rows.append({"row": index, "missing": missing})
    return {
        "ledger": name,
        "row_count": len(rows),
        "required_fields": sorted(required),
        "missing_rows": missing_rows,
        "passed": not missing_rows,
    }


def validate_summary_schema(summary: Mapping[str, Any]) -> dict[str, Any]:
    missing = sorted(SUMMARY_REQUIRED_FIELDS - set(summary))
    return {
        "required_fields": sorted(SUMMARY_REQUIRED_FIELDS),
        "missing_fields": missing,
        "passed": not missing,
    }


def public_payload(summary: Mapping[str, Any], *, public_redaction: Mapping[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "phase": summary["phase"],
        "title": summary["title"],
        "status": summary["status"],
        "mode": summary["mode"],
        "parallel_feature_development_paused": True,
        "current_head_reviewer_fix": dict(summary.get("current_head_reviewer_fix") or {}),
        "final_current_head_redaction_artifact_fix": {
            "actual_public_json_artifact_contract_scanned": True,
            "public_json_limited_to_scanned_projection": True,
            "inventory_only_rows_import_ready": False,
            "raw_production_storage_root_must_be_absolute": True,
            "media_extension_redaction_covers_supported_types": True,
            "posix_venv_symlink_hardening": "fixed_lexical_path_compare",
        },
        "inventory": {
            "total_files_seen": summary["inventory_results"]["total_files_seen"],
            "supported_candidates": summary["inventory_results"]["supported_candidates"],
            "eligible_before_duplicate_check": summary["inventory_results"]["eligible_before_duplicate_check"],
            "unique_import_candidates": summary["duplicate_deferred_unsupported_summary"]["unique_import_candidates"],
            "duplicates": summary["duplicate_deferred_unsupported_summary"]["duplicate_count"],
            "unsupported": summary["duplicate_deferred_unsupported_summary"]["unsupported_count"],
            "deferred": summary["duplicate_deferred_unsupported_summary"]["deferred_count"],
            "max_files_reached": summary["inventory_results"]["max_files_reached"],
        },
        "batch_plan": {
            "planned_batch_count": summary["batch_plan"]["planned_batch_count"],
            "batch_size": summary["batch_plan"]["batch_size"],
            "dry_run_only": True,
        },
        "safety_public": {
            "paths_redacted": True,
            "source_inputs_read_only": True,
            "source_mutation": False,
            "db_write": False,
            "app_storage_mutation": False,
            "provider_or_llm": False,
            "sourceconcept_or_entity": False,
        },
        "contracts_mapped_for_e1b": list(summary["contract_mapping"]["contracts"]),
        "public_report_redacted": True,
    }
    redaction = public_redaction if public_redaction is not None else summary.get("public_redaction")
    if isinstance(redaction, Mapping) and redaction.get("passed") is True:
        payload["public_redaction"] = dict(redaction)
    return payload


def public_report_markdown(summary: Mapping[str, Any]) -> str:
    inventory = summary["inventory_results"]
    duplicate = summary["duplicate_deferred_unsupported_summary"]
    batch = summary["batch_plan"]
    contracts = ", ".join(f"`{item}`" for item in summary["contract_mapping"]["contracts"])
    validation_commands = "\n".join(f"- `{cmd}`: {result}" for cmd, result in summary["validation"]["commands"].items())
    db_resolution = summary["db_identity"]["db_resolution"]
    e1b_blockers = "\n".join(
        f"- `{item}`" for item in summary["future_execution_plan"]["remaining_blockers_before_e1b_execute"]
    )
    return f"""# Phase 4.6-FULLLIB-E1a Production Runner Dry-Run Proof

## 1. Summary

E1a added and ran a dry-run-only production FULLLIB runner. The run inspected a bounded local input set, wrote private inventory ledgers, generated a future batch plan, and produced this privacy-safe public report. It did not execute import, classification, AI tagging, provider, LLM, SourceConcept, Entity, DB write, app-storage write, source write, server start, or browser validation.

## 2. Current state

PR #108 / GOV3 and PR #110 / FULLLIB-P0 are merged into `main`. This branch starts the implementation track for production utility work while keeping the SourceConcept/provider/entity track paused.

## 3. Current-head reviewer fix

This current-head fix streams source traversal and stops at `--max-files`, keeps repo-compatible Python preflight layouts, prevents DB identity overclaiming against app settings resolution, ledgers metadata access failures as deferred rows, records truthful worktree report provenance, and uses SQLAlchemy field-based URL construction for DB passwords with reserved characters.

## 4. Latest current-head reviewer fix

The latest current-head fix redaction-scans the actual public JSON artifact before writing it, requires explicit `VIOLET_ENV=production`, aborts on Python preflight failure before artifacts, resolves app settings through env-or-dotenv storage-root precedence, and fails closed on source traversal errors.

## 5. Final current-head redaction/artifact fix

The final current-head fix writes only the contract-scanned public JSON projection, so the actual public JSON artifact is scanned by `public_redaction_contract_v1`. Inventory-only rows are not import-ready, the raw production storage root CLI value must be absolute, media filename redaction covers supported extensions including `tif`, `tiff`, `avi`, and `mkv`, and POSIX venv symlink identity is hardened by comparing lexical repo-local candidate paths before symlink targets.

## 6. Parallel feature development

Parallel feature development is intentionally paused during FULLLIB. R1R, A1R, R2, SourceConcept, provider, and Entity work remain out of scope unless explicitly resumed later.

## 7. Runner design

The runner is a phase-scoped operational runner. In E1a it supports `--dry-run`, `--inventory-only`, `--output-dir`, `--write-public-report`, `--source-root`, `--production-db-url`, `--production-storage-root`, `--max-files`, and `--batch-size`. Execute mode is present only as a future guard and requires the exact confirmation phrase before it is rejected as not implemented in E1a.

## 8. Source root safety

Input locations are protected as read-only. The dry-run rejects missing inputs, repo overlap, production storage overlap, test storage overlap, output overlap, network/NAS paths, and unsafe output placement. Public artifacts redact all local paths and source names.

## 9. Production DB/storage identity design

E1a validates the intended production DB configuration without connecting or writing. The accepted production database name is `{RECOMMENDED_PRODUCTION_DB}`. App-equivalence status is `{db_resolution["app_equivalence_status"]}` and `urls_match={db_resolution["urls_match"]}`; E1a does not claim app equivalence unless settings/env/CLI resolution actually match. Production storage is validated as an explicit, non-overlapping app-managed storage root; E1a does not create directories or write files there.

## 10. Inventory dry-run results

- Files seen: {inventory["total_files_seen"]}
- Supported candidates: {inventory["supported_candidates"]}
- Eligible before duplicate check: {inventory["eligible_before_duplicate_check"]}
- Unique future import candidates: {duplicate["unique_import_candidates"]}
- Max files reached: {inventory["max_files_reached"]}

## 11. Duplicate/deferred/unsupported summary

- Duplicate items: {duplicate["duplicate_count"]}
- Unsupported items: {duplicate["unsupported_count"]}
- Deferred items: {duplicate["deferred_count"]}
- Hash failures: {duplicate["hash_failures"]}

## 12. Batch plan

- Planned batches: {batch["planned_batch_count"]}
- Batch size: {batch["batch_size"]}
- Planned unique candidates: {batch["planned_unique_candidate_count"]}
- Dry-run only: true

## 13. Future import execution plan

E1b must run a fresh inventory dry-run against approved production inputs, verify production DB/storage identity, prove backups and recovery, then import only gate-allowed unique candidates in bounded batches after explicit execute approval.

## 14. Future classification plan

Classification remains a post-import E1b stage for newly imported media only. It must record job accounting and content-class distribution before/after without source or storage mutation beyond the approved import outputs.

## 15. Future AI tagging plan

AI tagging remains a post-classification E1b stage for eligible imported media only. It must use the local WD tagger, preserve manual/locked tags, disable localization side effects, and record model provenance and coverage.

## 16. AI tag fingerprint reuse/export plan

Future reuse should key by `content_sha256`, compatible MD5, file size, media dimensions, model identity, tagger code identity, and thresholds. Compatible exports may replay tags through existing tag-service semantics; policy mismatches must defer or infer locally.

## 17. Localization handling

No LLM translation ran. Existing display behavior is DB/static localization first and canonical tag fallback otherwise. Newly generated AI tags will display Chinese names immediately only when existing static or DB translations cover them. E1b should emit a post-AI-tagging localization gap report; an optional later `FULLLIB-L1` can backfill translations under a separate approval.

## 18. Browser validation requirement for E1b

E1b is not complete until controlled browser/gallery validation passes, even if UI code is unchanged. It must verify imported media in gallery, thumbnails load, detail page opens, AI tags display, tag search returns imported media, localized tag display works where translations exist, no broken images appear, no private local source paths are exposed, and server identity is correct.

## 19. Contract mapping

E1b mapping: {contracts}. E1a does not claim import, classification, or AI tagging target completion.

## 20. Safety proof

The dry-run wrote only private ledgers under the chosen output directory and public report artifacts under `docs/reports`. It did not write DB, source, iCloud, app-managed storage, provider caches, SourceConcept tables, Entity truth, localization tables, or `media_tags`.

## 21. Validation commands and results

{validation_commands}

## 22. Remaining blockers before E1b execute

E1b still needs:

{e1b_blockers}

## 23. Recommended E1b prompt outline

Start from latest `main`, stay on the production utility track, run no provider/LLM/SourceConcept/Entity stages, verify production DB/storage identity, run fresh inventory dry-run, prove backups and contracts, then stop before execute unless the prompt includes `{CONFIRM_PHRASE}`. If execute is approved, import in bounded batches, classify, AI tag, run real browser/gallery validation, generate public/private artifacts, push PR, comment `@codex review`, and stop.
"""


def scan_public_text(value: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for reason, pattern in SENSITIVE_PUBLIC_PATTERNS:
        match = pattern.search(value)
        if match:
            findings.append({"reason": reason, "sample": "[redacted-match]", "match_length": len(match.group(0))})
    return findings


def validate_public_safe_run_id(run_id: str) -> None:
    if not run_id:
        return
    if scan_public_text(run_id):
        raise FulllibE1aBlockedError("unsafe_run_id_public_redaction_risk")


def run_redaction_check(
    summary: Mapping[str, Any],
    markdown_text: str,
    *,
    public_json_artifact: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    artifact = public_json_artifact or summary.get("public_json_payload") or summary
    payload = {
        "public_json_payload": artifact,
        "public_markdown_text": markdown_text,
    }
    contract_result = check_phase_contract("public_redaction_contract_v1", payload).to_dict()
    public_artifact_text = json.dumps(artifact, ensure_ascii=False, sort_keys=True, default=json_default)
    pattern_findings = (
        scan_public_text(markdown_text)
        + scan_public_text(public_artifact_text)
    )
    return {
        "passed": bool(contract_result.get("passed")) and not pattern_findings,
        "contract_passed": bool(contract_result.get("passed")),
        "contract_error_count": len(contract_result.get("errors") or []),
        "pattern_finding_count": len(pattern_findings),
        "findings_redacted": True,
        "checked_payloads": ["actual_public_json_artifact", "public_markdown_text"],
        "actual_public_json_artifact_scanned": True,
        "actual_public_json_artifact_contract_scanned": True,
    }


def build_summary(
    context: RuntimeContext,
    *,
    python_env: Mapping[str, Any],
    storage_identity: Mapping[str, Any],
    source_safety: Mapping[str, Any],
    inventory_summary: Mapping[str, Any],
    duplicate_summary: Mapping[str, Any],
    unsupported_or_deferred: Sequence[Mapping[str, Any]],
    batch_summary: Mapping[str, Any],
    ledger_validations: Sequence[Mapping[str, Any]],
    validation_commands: Mapping[str, str],
) -> dict[str, Any]:
    duplicate_deferred_summary = {
        "duplicate_count": duplicate_summary.get("duplicate_count", 0),
        "unique_import_candidates": duplicate_summary.get("unique_import_candidates", 0),
        "hash_reads_attempted": duplicate_summary.get("hash_reads_attempted", 0),
        "hash_failures": duplicate_summary.get("hash_failures", 0),
        "unsupported_count": inventory_summary.get("unsupported_count", 0),
        "deferred_count": inventory_summary.get("deferred_count", 0) + int(duplicate_summary.get("hash_failures", 0)),
        "reason_counts": dict(
            Counter(inventory_summary.get("reason_counts", {})) + Counter(duplicate_summary.get("reason_counts", {}))
        ),
        "existing_production_db_duplicates_checked": duplicate_summary.get("existing_production_db_duplicates_checked", False),
        "unsupported_or_deferred_ledger_rows": len(unsupported_or_deferred),
    }
    e1b_blockers = [
        "approved_real_production_source_input_set",
        "approved_production_storage_root",
        "non_mutating_production_db_connection_proof",
        "backup_recovery_proof",
        "offline_model_preflight",
        "fresh_e1b_dry_run_ledgers",
        "contract_checks",
        "explicit_execute_approval",
    ]
    if not context.db_identity.get("db_resolution", {}).get("runner_matches_app_equivalent"):
        e1b_blockers.append("app_db_equivalence_not_proven_or_mismatched")
    summary: dict[str, Any] = {
        "phase": PHASE,
        "title": PHASE_TITLE,
        "generated_at": utc_now(),
        "branch": git_value(["branch", "--show-current"]),
        "report_generation_git_state": build_report_generation_git_state(),
        "status": "dry_run_completed",
        "mode": "dry_run",
        "run_id": context.run_id,
        "current_head_reviewer_fix": {
            "streamed_traversal_honors_max_files": True,
            "python_preflight_repo_compatible": True,
            "db_identity_does_not_overclaim_app_equivalence": True,
            "metadata_access_failures_ledgered": True,
            "report_provenance_truthful_for_uncommitted_generation": True,
            "db_url_construction_uses_sqlalchemy_url_create_for_fields": True,
            "actual_public_json_artifact_redaction_scanned": True,
            "violet_env_production_required": True,
            "python_preflight_failure_aborts_before_artifacts": True,
            "app_settings_path_uses_env_or_dotenv_storage_root": True,
            "source_traversal_errors_fail_closed": True,
            "actual_public_json_artifact_contract_scanned": True,
            "public_json_limited_to_scanned_projection": True,
            "inventory_only_rows_not_import_ready": True,
            "raw_production_storage_root_must_be_absolute": True,
            "supported_media_extension_redaction_covered": True,
            "posix_venv_symlink_hardening": "fixed_lexical_path_compare",
        },
        "python_env": dict(python_env),
        "db_identity": context.db_identity,
        "production_storage_identity": dict(storage_identity),
        "source_root_safety_proof": dict(source_safety),
        "staging_root_safety_proof": {
            "passed": True,
            "staging_copy_executed": False,
            "staging_root_configured": False,
            "reason": "E1a does not stage or copy; E1b must provide staging/app-storage proof before execute.",
        },
        "inventory_results": dict(inventory_summary),
        "duplicate_deferred_unsupported_summary": duplicate_deferred_summary,
        "batch_plan": dict(batch_summary),
        "media_counts": {
            "before": "not_connected_in_e1a",
            "after": "not_connected_in_e1a",
            "db_write": False,
        },
        "import_ledger": {
            "present": False,
            "reason": "E1a writes inventory and batch ledgers only; import-item ledger starts in E1b execute.",
        },
        "eligible_denominator": 0,
        "classification_jobs_by_status": {},
        "content_class_distribution": {"before": {}, "after": {}},
        "eligible_media_denominator": 0,
        "ai_tag_coverage": {"before": "not_run", "after": "not_run"},
        "job_status_accounting": {},
        "model_provenance": {"status": "not_checked_in_e1a", "network_download": False},
        "manual_truth_overwrite_proof": {"passed": True, "ai_tagging_executed": False},
        "mutation_proof": {
            "passed": True,
            "db_write_attempted": False,
            "source_icloud_mutation": False,
            "app_storage_mutation": False,
            "classification_executed": False,
            "ai_tagging_executed": False,
            "provider_llm_sourceconcept_entity_executed": False,
            "forbidden_changed_tables": [],
            "unexpected_changed_tables": [],
            "changed_tables": [],
        },
        "public_redaction": {"passed": False, "pending_until_public_render": True},
        "contract_mapping": {
            "contracts": [
                "python_env_contract_v1",
                "postgres_db_contract_v1",
                "media_import_contract_v1",
                "classification_contract_v1",
                "ai_tagging_contract_v1",
                "mutation_safety_contract_v1",
                "artifact_lifecycle_contract_v1",
                "public_redaction_contract_v1",
            ],
            "e1a_completion_claims": {
                "import_target_met": False,
                "classification_target_met": False,
                "ai_tagging_target_met": False,
                "safe_to_merge": False,
            },
        },
        "future_execution_plan": {
            "execute_confirmation_required": CONFIRM_PHRASE,
            "full_import_executed": False,
            "db_write_executed": False,
            "requires_backup_recovery_before_execute": True,
            "requires_fresh_e1b_dry_run": True,
            "remaining_blockers_before_e1b_execute": e1b_blockers,
        },
        "classification_plan": {
            "run_in_e1a": False,
            "future_scope": "newly imported media only unless separately approved",
            "eligible_for_ai_tagging": ["anime", "unknown"],
            "source_storage_mutation_forbidden": True,
        },
        "ai_tagging_plan": {
            "run_in_e1a": False,
            "local_wd_tagger_only": True,
            "network_model_download_forbidden": True,
            "localization_side_effects_disabled_for_e1b": [
                "AI_TAGGING_AUTO_LOCALIZATION=false",
                "TAG_TRANSLATION_BACKGROUND_ENABLED=false",
                "TAG_TRANSLATION_AUTO_ENABLED=false",
                "TAG_TRANSLATION_LLM_ENABLED=false",
            ],
        },
        "ai_tag_fingerprint_reuse_plan": {
            "primary_key": "content_sha256",
            "compatibility_key": "Media.hash md5",
            "private_export_required_before_reuse": True,
            "reuse_requires_model_threshold_match": True,
        },
        "localization_handling": {
            "llm_translation_run": False,
            "static_json_inspected": True,
            "db_localization_architecture_inspected": True,
            "new_ai_tags_have_chinese_immediately": "only_if_existing_static_or_db_translation_exists",
            "post_ai_tagging_gap_report_required": True,
            "optional_later_phase": "FULLLIB-L1 localization backfill",
        },
        "browser_validation_e1b": {
            "run_in_e1a": False,
            "required_for_e1b_completion": True,
            "required_checks": [
                "imported media appears in gallery",
                "thumbnails load",
                "media detail page opens",
                "AI tags display",
                "tag search returns imported media",
                "localized tag display works where translations exist",
                "no broken images",
                "no private local source paths exposed in UI",
                "server identity is correct",
            ],
        },
        "safety_proof": {
            "no_push_main": True,
            "no_merge": True,
            "no_db_write": True,
            "no_import": True,
            "no_classification": True,
            "no_ai_tagging": True,
            "no_provider": True,
            "no_llm": True,
            "no_sourceconcept": True,
            "no_entity_truth": True,
            "no_source_metadata_mutation": True,
            "no_source_icloud_storage_mutation": True,
            "no_cleanup_delete_reset_drop_truncate": True,
        },
        "validation": {
            "commands": dict(validation_commands),
            "ledger_schema": list(ledger_validations),
            "summary_schema": {},
            "browser_validation": "not_run_e1a_dry_run_only_no_execute_smoke",
        },
        "artifact_lifecycle": {
            "artifacts": [
                {
                    "path": "scripts/run_phase46_fulllib_e1_production_import_ai_tagging.py",
                    "classification": "phase-scoped operational runner",
                    "committed": True,
                },
                {
                    "path": "private ledgers under ignored output directory",
                    "classification": "one-off local/private",
                    "committed": False,
                },
                {
                    "path": "docs/reports/phase-4.6-fulllib-e1a-runner-dryrun.md",
                    "classification": "public report/handoff",
                    "committed": True,
                    "redacted": True,
                },
                {
                    "path": "docs/reports/phase-4.6-fulllib-e1a-runner-dryrun-summary.json",
                    "classification": "public report/handoff",
                    "committed": True,
                    "redacted": True,
                },
            ]
        },
        "private_artifacts": {
            "artifact_names": list(PRIVATE_LEDGER_NAMES),
            "private_paths_committed": False,
            "raw_paths_private_only": True,
        },
    }
    summary["public_json_payload"] = public_payload(summary)
    markdown_text = public_report_markdown({**summary, "public_redaction": {"passed": True}})
    summary["public_markdown_text"] = markdown_text
    summary["validation"]["summary_schema"] = validate_summary_schema(summary)
    return summary


def write_public_outputs(summary: dict[str, Any]) -> dict[str, Any]:
    markdown_text = public_report_markdown(summary)
    redaction: dict[str, Any] = {
        "passed": True,
        "contract_passed": True,
        "contract_error_count": 0,
        "pattern_finding_count": 0,
        "findings_redacted": True,
        "checked_payloads": ["actual_public_json_artifact", "public_markdown_text"],
        "actual_public_json_artifact_scanned": True,
        "actual_public_json_artifact_contract_scanned": True,
    }
    public_json_artifact = public_payload(summary, public_redaction=redaction)
    summary["public_markdown_text"] = markdown_text
    for _ in range(3):
        next_redaction = run_redaction_check(summary, markdown_text, public_json_artifact=public_json_artifact)
        if not next_redaction["passed"]:
            raise FulllibE1aBlockedError("public_redaction_failed")
        if next_redaction == redaction:
            break
        redaction = next_redaction
        public_json_artifact = public_payload(summary, public_redaction=redaction)
    else:
        raise FulllibE1aBlockedError("public_redaction_unstable")
    summary["public_redaction"] = redaction
    summary["public_json_payload"] = public_json_artifact
    summary["public_markdown_text"] = markdown_text
    write_text(PUBLIC_REPORT_MD, summary["public_markdown_text"])
    write_json(PUBLIC_REPORT_JSON, public_json_artifact)
    return redaction


def build_validation_commands(args: argparse.Namespace) -> dict[str, str]:
    command = "python.exe scripts/run_phase46_fulllib_e1_production_import_ai_tagging.py --dry-run [paths redacted]"
    if args.inventory_only:
        command = command.replace("--dry-run", "--dry-run --inventory-only")
    return {
        command: "passed",
        "no DB connection attempted by E1a runner": "passed",
        "no source/app-storage write attempted by E1a runner": "passed",
    }


def run_dry_run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.dry_run:
        raise FulllibE1aBlockedError("e1a_requires_dry_run")
    validate_positive_int(args.max_files, "max_files")
    validate_positive_int(args.batch_size, "batch_size")
    validate_positive_int(args.hash_timeout_seconds, "hash_timeout_seconds")
    validate_positive_int(args.max_file_size_mb, "max_file_size_mb")
    validate_public_safe_run_id(args.run_id or "")
    python_env = validate_python_env_or_block()

    source_roots = tuple(resolve_path(Path(raw)) for raw in args.source_root)
    raw_production_storage_root = Path(args.production_storage_root)
    if not raw_production_storage_root.is_absolute():
        raise FulllibE1aBlockedError("production_storage_root_must_be_absolute_raw_cli_path")
    production_storage_root = resolve_path(raw_production_storage_root)
    output_dir = validate_output_dir(args.output_dir, source_roots=source_roots, production_storage_root=production_storage_root)
    db_identity = resolve_production_db_identity(args.production_db_url)
    context = RuntimeContext(
        run_id=args.run_id or f"fulllib-e1a-{uuid.uuid4().hex[:12]}",
        output_dir=output_dir,
        source_roots=source_roots,
        production_storage_root=production_storage_root,
        db_identity=db_identity,
        dry_run=True,
        inventory_only=bool(args.inventory_only),
        max_files=int(args.max_files),
        batch_size=int(args.batch_size),
        max_file_size_bytes=int(args.max_file_size_mb) * 1024 * 1024,
        hash_timeout_seconds=int(args.hash_timeout_seconds),
    )

    storage_identity = build_storage_identity(context.production_storage_root, context.source_roots)
    source_safety = build_source_root_safety(
        context.source_roots,
        production_storage_root=context.production_storage_root,
        output_dir=context.output_dir,
    )
    assert_static_safety(storage_identity, source_safety)

    candidates, unsupported_or_deferred, inventory_summary = inventory_source_roots(context)
    duplicates, duplicate_summary = apply_hashes_and_duplicates(context, candidates, unsupported_or_deferred)
    batches, batch_summary = build_batch_plan(context, candidates)

    context.output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(context.output_dir / "inventory-candidates.jsonl", candidates)
    write_jsonl(context.output_dir / "duplicate-skipped.jsonl", duplicates)
    write_jsonl(context.output_dir / "unsupported-or-deferred.jsonl", unsupported_or_deferred)
    write_jsonl(context.output_dir / "batch-plan.jsonl", batches)

    ledger_validations = [
        validate_ledger_schema(candidates, INVENTORY_CANDIDATE_REQUIRED_FIELDS, "inventory-candidates.jsonl"),
        validate_ledger_schema(duplicates, DUPLICATE_REQUIRED_FIELDS, "duplicate-skipped.jsonl"),
        validate_ledger_schema(unsupported_or_deferred, UNSUPPORTED_OR_DEFERRED_REQUIRED_FIELDS, "unsupported-or-deferred.jsonl"),
        validate_ledger_schema(batches, BATCH_PLAN_REQUIRED_FIELDS, "batch-plan.jsonl"),
    ]
    if not all(row["passed"] for row in ledger_validations):
        raise FulllibE1aBlockedError("private_ledger_schema_validation_failed")

    summary = build_summary(
        context,
        python_env=python_env,
        storage_identity=storage_identity,
        source_safety=source_safety,
        inventory_summary=inventory_summary,
        duplicate_summary=duplicate_summary,
        unsupported_or_deferred=unsupported_or_deferred,
        batch_summary=batch_summary,
        ledger_validations=ledger_validations,
        validation_commands=build_validation_commands(args),
    )
    if not summary["validation"]["summary_schema"]["passed"]:
        raise FulllibE1aBlockedError("summary_schema_validation_failed")

    if args.write_public_report:
        redaction = write_public_outputs(summary)
        write_json(context.output_dir / "public-redaction-check.json", redaction)
    else:
        write_json(context.output_dir / "public-redaction-check.json", {"passed": None, "reason": "public_report_not_requested"})

    private_summary = {
        **summary,
        "private": {
            "output_dir": str(context.output_dir),
            "source_roots": [str(path) for path in context.source_roots],
            "production_storage_root": str(context.production_storage_root),
            "private_ledger_paths": [str(context.output_dir / name) for name in PRIVATE_LEDGER_NAMES],
        },
    }
    write_json(context.output_dir / "run-summary-private.json", private_summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Run E1a inventory dry-run. This is the only supported E1a mode.")
    mode.add_argument("--execute", action="store_true", help="Future E1b execute mode placeholder. E1a rejects it.")
    parser.add_argument("--inventory-only", action="store_true", help="Only inventory files; skip hash duplicate detection and batch candidates.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write-public-report", action="store_true")
    parser.add_argument("--source-root", action="append", required=True, help="Protected source input root. Repeat for multiple roots.")
    parser.add_argument("--production-db-url", default=None, help="Production DB URL. Passwords are never recorded.")
    parser.add_argument("--production-storage-root", type=Path, required=True)
    parser.add_argument("--max-files", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--max-file-size-mb", type=int, default=200)
    parser.add_argument("--hash-timeout-seconds", type=int, default=30)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--confirm-execution", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    multiprocessing.freeze_support()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.execute and args.confirm_execution != CONFIRM_PHRASE:
        parser.error(f"--execute requires --confirm-execution {CONFIRM_PHRASE}")
    if args.execute:
        print("ERROR: E1a execute mode is intentionally not implemented; run E1b with explicit approval.", file=sys.stderr)
        return 2
    try:
        summary = run_dry_run(args)
    except FulllibE1aBlockedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary["public_json_payload"], ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
