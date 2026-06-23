#!/usr/bin/env python3
"""Windows-first production launcher control plane for V.I.O.L.E.T.

This module intentionally avoids importing backend.app.config during
preflight. The application settings object creates runtime directories and can
write settings.json on import, which is the wrong shape for a safety gate.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import ntpath
import os
import platform
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".local_manifests" / "production_launcher"
STATE_FILE = STATE_DIR / "violet-production-launcher-state.json"
LOG_FILE = STATE_DIR / "violet-production.log"
START_LOCK_FILE = STATE_DIR / "violet-production-launcher-start.lock"
DEFAULT_PROFILE_ID = "production-default"
PROFILE_FILE_NAME = "production-profile.json"
RUN_PY = ROOT / "run.py"

APP_NAME = "V.I.O.L.E.T."
STATE_VERSION = 1
DEFAULT_PORT = 8000
HEALTH_PATH = "/api/health"
READY_TIMEOUT_SECONDS = 45.0
START_LOCK_TTL_SECONDS = 600.0

AUTOMATION_FLAGS = (
    "DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED",
    "AI_AUTO_TAG_AFTER_IMPORT",
    "CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT",
    "TAG_TRANSLATION_AUTO_ENABLED",
    "TAG_TRANSLATION_BACKGROUND_ENABLED",
)

PROFILE_AUTOMATION_FLAGS = {
    "dynamic_library_auto_sync": "DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED",
    "ai_auto_tag_after_import": "AI_AUTO_TAG_AFTER_IMPORT",
    "content_classification_auto_after_import": "CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT",
    "tag_translation_auto": "TAG_TRANSLATION_AUTO_ENABLED",
    "tag_translation_background": "TAG_TRANSLATION_BACKGROUND_ENABLED",
}

DANGEROUS_PRODUCTION_FLAGS = (
    "VIOLET_ALLOW_DESTRUCTIVE_E2E",
    "VIOLET_RUN_REAL_E2E",
)

PRODUCTION_PROFILE_ENV_ALLOWLIST = {
    "APPDATA",
    "COMSPEC",
    "CURL_CA_BUNDLE",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMW6432",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "TZ",
    "USERPROFILE",
    "WINDIR",
}

STARTUP_SAFE_MODE_ENV = "VIOLET_PRODUCTION_LAUNCHER_SAFE_STARTUP"
STARTUP_MAINTENANCE_APPROVAL_ENV = "VIOLET_PRODUCTION_STARTUP_MAINTENANCE_APPROVED"

NORMAL_STARTUP_MAINTENANCE = (
    "init_engine",
    "init_db_create_all_and_schema_migration",
    "upload_temp_chunk_cleanup",
    "stale_scan_ai_translation_classification_job_recovery",
    "static_tag_translation_seed",
    "periodic_upload_temp_cleanup_task",
    "background_tag_translation_worker_if_enabled",
)

FORBIDDEN_START_TOKENS = (
    "--debug",
    "run_s3a",
    "s3a_pilot",
    "gallery-dl",
    "gallery_dl",
    "saucenao",
    "pixiv",
    "sourceconcept",
    "source_concept",
    "entity_bridge",
    "import_staged_manifest",
    "scan-local-library",
)


@dataclass
class RuntimeConfig:
    repo_root: Path
    dotenv_path: Path
    env: dict[str, str]
    storage_root: Path | None
    settings_path: Path | None
    settings_json: dict[str, Any]
    db: dict[str, Any]
    port: int
    url: str
    expected_python: Path
    accepted_storage_root: Path | None = None
    port_raw: str = ""
    port_resolved: bool = True
    port_error: str | None = None
    db_port_raw: str = ""
    db_port_valid: bool = True
    db_port_error: str | None = None
    config_source: str = "development_env"
    profile_id: str | None = None
    profile_path: Path | None = None
    profile_exists: bool = False
    profile_data: dict[str, Any] = field(default_factory=dict)
    profile_errors: list[str] = field(default_factory=list)


@dataclass
class ProcessSnapshot:
    pid: int
    exists: bool
    command_line: str = ""
    executable_path: str = ""
    create_time: float | None = None
    cwd: str = ""
    parent_pid: int | None = None


@dataclass
class StartLockStatus:
    acquired: bool
    stale_reclaimed: bool = False
    reason: str | None = None


@dataclass
class Gate:
    name: str
    passed: bool
    message: str
    hard: bool = True

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "hard": self.hard,
            "message": self.message,
        }


@dataclass
class ControlResult:
    ok: bool
    status: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    gates: list[Gate] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        payload = {
            "ok": self.ok,
            "status": self.status,
            "message": self.message,
            "errors": list(self.errors),
            "data": _public_payload(self.data),
        }
        if self.gates:
            payload["gates"] = [gate.to_public_dict() for gate in self.gates]
        return payload


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def public_error(exc: BaseException) -> str:
    text = str(exc).strip()
    if not text:
        return exc.__class__.__name__
    return f"{exc.__class__.__name__}: {_redact_private_text(text)}"


def _truthy(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"true", "1", "yes", "on"}


def _parse_port(value: Any, *, default: int = DEFAULT_PORT) -> tuple[int, bool, str | None, str]:
    raw = str(value if value is not None else default).strip()
    if not raw:
        raw = str(default)
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return default, False, "APP_PORT must be an integer between 1 and 65535.", raw
    if not (1 <= port <= 65535):
        return default, False, "APP_PORT must be between 1 and 65535.", raw
    return port, True, None, raw


def _parse_named_port(name: str, value: Any, *, default: int = 5432, explicit: bool = False) -> tuple[int, bool, str | None, str]:
    raw = str(value if value is not None else "").strip()
    if not raw and not explicit:
        return default, True, None, str(default)
    if not raw and explicit:
        return default, False, f"{name} must be an integer between 1 and 65535.", raw
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return default, False, f"{name} must be an integer between 1 and 65535.", raw
    if not (1 <= port <= 65535):
        return default, False, f"{name} must be between 1 and 65535.", raw
    return port, True, None, raw


def _parse_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_path(value: str | Path | None) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    looks_windows = bool(len(text) > 2 and text[1] == ":") or "\\" in text
    if looks_windows:
        return ntpath.normpath(text).replace("\\", "/").lower().rstrip("/")
    return os.path.normpath(text).replace("\\", "/").lower().rstrip("/")


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _redact_private_text(text: str) -> str:
    replacements = [
        str(ROOT),
        str(STATE_DIR),
        str(STATE_FILE),
        str(LOG_FILE),
    ]
    for raw in replacements:
        if raw:
            text = text.replace(raw, "[repo-local]")
            text = text.replace(raw.replace("\\", "/"), "[repo-local]")
    text = re.sub(
        r"\[repo-local\][\\/]+\.local_manifests[\\/]+production_launcher(?:[\\/]+production-profile\.json)?",
        "[repo-local]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(^|[^\w])\.local_manifests[\\/]+production_launcher(?:[\\/]+production-profile\.json)?",
        r"\1[profile-path]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"(^|[^:])(?:\\\\|//)[A-Za-z0-9._$-]+[\\/][^\s'\"`<>|]+", r"\1[unc-path]", text)
    text = re.sub(r"(^|[^A-Za-z])([A-Za-z]:[\\/][^\s'\"`<>|]+)", r"\1[path]", text)
    text = re.sub(r"/(?:Users|home)/[^\s'\"`<>|]+", "[path]", text)
    text = re.sub(r"\b(Bearer)\s+[A-Za-z0-9._~+/=-]+", r"\1 [redacted]", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(password|passwd|token|secret|api[_-]?key)\s*[:=]\s*[^\s'\"`]+", r"\1=[redacted]", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]{8,}|xox[baprs]-[A-Za-z0-9-]{8,})\b", "[token]", text)
    return text


def _public_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        public: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in {
                "repo_root",
                "storage_root",
                "settings_path",
                "dotenv_path",
                "state_file",
                "log_file",
                "recent_log_tail",
                "log_tail",
                "command",
                "python_executable",
            }:
                continue
            public[key_text] = _public_payload(item)
        return public
    if isinstance(value, list):
        return [_public_payload(item) for item in value]
    if isinstance(value, str):
        return _redact_private_text(value)
    return value


def startup_write_policy_public(config: RuntimeConfig | None = None) -> dict[str, Any]:
    operator_intent = False
    if config is not None:
        operator_intent = _truthy(config.env.get(STARTUP_MAINTENANCE_APPROVAL_ENV))
    return {
        "normal_startup_maintenance_documented": True,
        "normal_startup_maintenance": list(NORMAL_STARTUP_MAINTENANCE),
        "launcher_safe_startup_mode_enabled": True,
        "schema_migration_allowed": False,
        "schema_migration_blocked_by_launcher_safe_mode": True,
        "destructive_cleanup_allowed": False,
        "upload_temp_cleanup_blocked_by_launcher_safe_mode": True,
        "import_tagging_sync_jobs_allowed": False,
        "operator_intent_required_for_startup_maintenance": True,
        "operator_intent_present": operator_intent,
    }


def parse_dotenv(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        data[key] = value
    return data


def load_settings_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def profile_file_for_repo(repo_root: Path = ROOT) -> Path:
    return repo_root / ".local_manifests" / "production_launcher" / PROFILE_FILE_NAME


def profile_file_is_local_ignored(path: Path, repo_root: Path = ROOT) -> bool:
    normalized = _normalize_path(path)
    root_normalized = _normalize_path(repo_root)
    return normalized.startswith(root_normalized + "/.local_manifests/production_launcher/")


def expected_venv_python(repo_root: Path) -> Path:
    candidates = [
        repo_root / "venv" / "Scripts" / "python.exe",
        repo_root / ".venv" / "Scripts" / "python.exe",
        repo_root / "venv" / "bin" / "python",
        repo_root / ".venv" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0] if platform.system() == "Windows" else candidates[2]


def _safe_dotenv_defaults(repo_root: Path) -> dict[str, str]:
    values = parse_dotenv(repo_root / ".env")
    safe_keys = {
        "APP_PORT",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "VIOLET_PRODUCTION_PYTHON",
        "VIOLET_CANONICAL_REPO_ROOT",
    }
    return {key: value for key, value in values.items() if key in safe_keys and str(value).strip()}


def _local_dotenv_profile_values(repo_root: Path) -> dict[str, str]:
    values = parse_dotenv(repo_root / ".env")
    local_keys = {
        "APP_PORT",
        "BLOMBOORU_REQUIRE_AUTH",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "VIOLET_STORAGE_ROOT",
        "VIOLET_PRODUCTION_STORAGE_ROOT",
        "VIOLET_ACCEPTED_PRODUCTION_STORAGE_ROOT",
        "VIOLET_PRODUCTION_PYTHON",
        "VIOLET_CANONICAL_REPO_ROOT",
    }
    return {key: value for key, value in values.items() if key in local_keys and str(value).strip()}


def _auth_policy_bool(value: Any, *, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().casefold()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off"}:
        return False
    return default


def _settings_candidates_for_profile(repo_root: Path, storage_root: str) -> list[Path]:
    candidates: list[Path] = []
    if storage_root:
        candidates.append(Path(storage_root).expanduser() / "data" / "settings.json")
    candidates.append(repo_root / "data" / "settings.json")
    seen: set[str] = set()
    unique: list[Path] = []
    for candidate in candidates:
        normalized = _normalize_path(candidate)
        if normalized and normalized not in seen:
            unique.append(candidate)
            seen.add(normalized)
    return unique


def _first_existing_settings_json(candidates: list[Path]) -> tuple[dict[str, Any], Path | None]:
    for candidate in candidates:
        if candidate.is_file():
            payload = load_settings_json(candidate)
            if payload:
                return payload, candidate
    return {}, None


def _default_profile_payload(repo_root: Path = ROOT, profile_id: str = DEFAULT_PROFILE_ID) -> dict[str, Any]:
    safe_defaults = _safe_dotenv_defaults(repo_root)
    app_port, app_port_ok, _app_port_error, _app_port_raw = _parse_port(safe_defaults.get("APP_PORT") or DEFAULT_PORT)
    if not app_port_ok:
        app_port = DEFAULT_PORT
    db_port, db_port_ok, _db_port_error, _db_port_raw = _parse_named_port(
        "POSTGRES_PORT",
        safe_defaults.get("POSTGRES_PORT"),
        explicit=bool(safe_defaults.get("POSTGRES_PORT")),
    )
    if not db_port_ok:
        db_port = 5432
    return {
        "profile_id": profile_id,
        "env": "production",
        "repo_root": str(repo_root),
        "python": safe_defaults.get("VIOLET_PRODUCTION_PYTHON") or str(expected_venv_python(repo_root)),
        "app_port": app_port,
        "storage_root": "",
        "require_auth": True,
        "db": {
            "host": safe_defaults.get("POSTGRES_HOST") or "localhost",
            "port": db_port,
            "name": safe_defaults.get("POSTGRES_DB") or "blombooru",
            "user": safe_defaults.get("POSTGRES_USER") or "postgres",
            "password": "",
        },
        "safe_startup": True,
        "automation_flags": {name: False for name in PROFILE_AUTOMATION_FLAGS},
    }


def _repair_profile_invariants(profile: Mapping[str, Any], *, repo_root: Path = ROOT, profile_id: str = DEFAULT_PROFILE_ID) -> dict[str, Any]:
    repaired = dict(profile)
    repaired["profile_id"] = profile_id
    repaired["env"] = "production"
    repaired["repo_root"] = str(repo_root)
    repaired["safe_startup"] = True
    repaired["require_auth"] = _auth_policy_bool(repaired.get("require_auth"), default=True)
    flags = dict(repaired.get("automation_flags", {}) if isinstance(repaired.get("automation_flags"), Mapping) else {})
    for key in PROFILE_AUTOMATION_FLAGS:
        flags[key] = False
    repaired["automation_flags"] = flags
    return repaired


def discover_local_profile_payload(
    repo_root: Path = ROOT,
    *,
    profile_id: str = DEFAULT_PROFILE_ID,
    existing: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    local_values = _local_dotenv_profile_values(repo_root)
    profile = _repair_profile_invariants(
        _coerce_profile_payload(existing, repo_root=repo_root, profile_id=profile_id),
        repo_root=repo_root,
        profile_id=profile_id,
    )

    storage_root = (
        str(profile.get("storage_root") or "").strip()
        or local_values.get("VIOLET_STORAGE_ROOT", "").strip()
        or local_values.get("VIOLET_PRODUCTION_STORAGE_ROOT", "").strip()
        or local_values.get("VIOLET_ACCEPTED_PRODUCTION_STORAGE_ROOT", "").strip()
    )
    if not storage_root and (repo_root / "data" / "settings.json").is_file():
        storage_root = str(repo_root)
    if storage_root:
        profile["storage_root"] = storage_root

    if local_values.get("VIOLET_PRODUCTION_PYTHON"):
        profile["python"] = local_values["VIOLET_PRODUCTION_PYTHON"]
    elif not Path(str(profile.get("python") or "")).is_file():
        profile["python"] = str(expected_venv_python(repo_root))

    if local_values.get("APP_PORT") and not str(profile.get("app_port") or "").strip():
        profile["app_port"] = local_values["APP_PORT"]

    settings_json, settings_path = _first_existing_settings_json(_settings_candidates_for_profile(repo_root, storage_root))
    if isinstance(existing, Mapping) and "require_auth" in existing:
        profile["require_auth"] = _auth_policy_bool(existing.get("require_auth"), default=True)
        auth_source = "existing_profile"
    elif "require_auth" in settings_json:
        profile["require_auth"] = _auth_policy_bool(settings_json.get("require_auth"), default=True)
        auth_source = "settings_json"
    elif local_values.get("BLOMBOORU_REQUIRE_AUTH"):
        profile["require_auth"] = _auth_policy_bool(local_values.get("BLOMBOORU_REQUIRE_AUTH"), default=True)
        auth_source = "dotenv"
    else:
        profile["require_auth"] = True
        auth_source = "safe_default_true"
    settings_db = settings_json.get("database", {}) if isinstance(settings_json.get("database"), Mapping) else {}
    existing_db = existing.get("db", {}) if isinstance(existing, Mapping) and isinstance(existing.get("db"), Mapping) else {}
    db = dict(profile.get("db", {}) if isinstance(profile.get("db"), Mapping) else {})
    for key, dotenv_key in (
        ("host", "POSTGRES_HOST"),
        ("port", "POSTGRES_PORT"),
        ("name", "POSTGRES_DB"),
        ("user", "POSTGRES_USER"),
        ("password", "POSTGRES_PASSWORD"),
    ):
        if existing_db.get(key) not in (None, ""):
            continue
        settings_value = settings_db.get(key) if isinstance(settings_db, Mapping) else None
        if settings_value not in (None, ""):
            db[key] = settings_value
        elif local_values.get(dotenv_key):
            db[key] = local_values[dotenv_key]
    profile["db"] = db

    inferred = {
        "storage_root_from": (
            "existing_profile"
            if existing and str(existing.get("storage_root") or "").strip()
            else ("local_settings" if storage_root and _normalize_path(storage_root) == _normalize_path(repo_root) else ("dotenv" if storage_root else None))
        ),
        "settings_path_found": settings_path is not None,
        "db_from_settings": bool(settings_db),
        "db_from_dotenv": any(local_values.get(key) for key in ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")),
        "auth_policy_from": auth_source,
        "local_access_values_written_to_profile": True,
    }
    return profile, inferred


def _load_profile_payload(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    if not path.exists():
        return None, ["profile_missing"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, ["profile_json_invalid"]
    except OSError:
        return None, ["profile_unreadable"]
    if not isinstance(payload, dict):
        return None, ["profile_not_object"]
    return payload, []


def _coerce_profile_payload(
    payload: Mapping[str, Any] | None,
    *,
    repo_root: Path = ROOT,
    profile_id: str = DEFAULT_PROFILE_ID,
) -> dict[str, Any]:
    base = _default_profile_payload(repo_root, profile_id)
    if not payload:
        return base
    profile = dict(base)
    for key in ("profile_id", "env", "repo_root", "python", "storage_root"):
        if key in payload and payload.get(key) is not None:
            profile[key] = str(payload.get(key))
    if "app_port" in payload:
        profile["app_port"] = payload.get("app_port")
    if "safe_startup" in payload:
        profile["safe_startup"] = bool(payload.get("safe_startup"))
    if "require_auth" in payload:
        profile["require_auth"] = _auth_policy_bool(payload.get("require_auth"), default=True)
    if isinstance(payload.get("db"), Mapping):
        db = dict(profile["db"])
        for key in ("host", "name", "user", "password"):
            if key in payload["db"] and payload["db"].get(key) is not None:
                db[key] = str(payload["db"].get(key))
        if "port" in payload["db"]:
            db["port"] = payload["db"].get("port")
        profile["db"] = db
    if isinstance(payload.get("automation_flags"), Mapping):
        flags = dict(profile["automation_flags"])
        for key in PROFILE_AUTOMATION_FLAGS:
            if key in payload["automation_flags"]:
                flags[key] = bool(payload["automation_flags"].get(key))
        profile["automation_flags"] = flags
    return profile


def load_production_profile(
    *,
    repo_root: Path = ROOT,
    profile_id: str = DEFAULT_PROFILE_ID,
    profile_path: Path | None = None,
) -> tuple[dict[str, Any] | None, Path, list[str]]:
    path = profile_path or profile_file_for_repo(repo_root)
    payload, errors = _load_profile_payload(path)
    if payload is None:
        return None, path, errors
    profile = _coerce_profile_payload(payload, repo_root=repo_root, profile_id=profile_id)
    if profile.get("profile_id") != profile_id:
        errors.append("profile_id_mismatch")
    return profile, path, errors


def write_production_profile(
    profile: Mapping[str, Any],
    *,
    repo_root: Path = ROOT,
    profile_path: Path | None = None,
) -> Path:
    path = profile_path or profile_file_for_repo(repo_root)
    if not profile_file_is_local_ignored(path, repo_root):
        raise ValueError("Production profile path must stay under .local_manifests/production_launcher.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        temp_path.write_text(json.dumps(dict(profile), indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
    return path


def _profile_to_env(profile: Mapping[str, Any], *, repo_root: Path = ROOT) -> dict[str, str]:
    db = profile.get("db", {}) if isinstance(profile.get("db"), Mapping) else {}
    storage_root = str(profile.get("storage_root") or "").strip()
    profile_env = {
        "VIOLET_ENV": "production",
        "BLOMBOORU_DEBUG": "false",
        "APP_PORT": str(profile.get("app_port") or DEFAULT_PORT),
        "BLOMBOORU_REQUIRE_AUTH": "true" if _auth_policy_bool(profile.get("require_auth"), default=True) else "false",
        "VIOLET_STORAGE_ROOT": storage_root,
        "VIOLET_CANONICAL_REPO_ROOT": str(profile.get("repo_root") or repo_root),
        "VIOLET_PRODUCTION_PYTHON": str(profile.get("python") or expected_venv_python(repo_root)),
        "VIOLET_PRODUCTION_PROFILE_ACTIVE": "true",
        "VIOLET_PRODUCTION_PROFILE_ID": str(profile.get("profile_id") or DEFAULT_PROFILE_ID),
        "VIOLET_SKIP_DOTENV": "1",
        STARTUP_SAFE_MODE_ENV: "true",
        STARTUP_MAINTENANCE_APPROVAL_ENV: "false",
        "POSTGRES_HOST": str(db.get("host") or "localhost"),
        "POSTGRES_PORT": str(db.get("port") or 5432),
        "POSTGRES_DB": str(db.get("name") or "blombooru"),
        "POSTGRES_USER": str(db.get("user") or "postgres"),
        "POSTGRES_PASSWORD": str(db.get("password") or ""),
    }
    for flag in AUTOMATION_FLAGS:
        profile_env[flag] = "false"
    for flag in DANGEROUS_PRODUCTION_FLAGS:
        profile_env[flag] = "false"
    return profile_env


def _production_profile_baseline(source_env: Mapping[str, str] | None = None) -> dict[str, str]:
    source = dict(source_env or os.environ)
    allowed = {key.casefold() for key in PRODUCTION_PROFILE_ENV_ALLOWLIST}
    baseline: dict[str, str] = {}
    for key, value in source.items():
        if key.casefold() in allowed:
            baseline[str(key)] = str(value)
    baseline.setdefault("PYTHONUNBUFFERED", "1")
    return baseline


def resolve_config(
    repo_root: Path = ROOT,
    *,
    base_env: Mapping[str, str] | None = None,
    profile_id: str | None = None,
    profile_path: Path | None = None,
) -> RuntimeConfig:
    dotenv_path = repo_root / ".env"
    profile, resolved_profile_path, profile_errors = (None, profile_path or profile_file_for_repo(repo_root), [])
    if profile_id is None:
        dotenv_values = parse_dotenv(dotenv_path)
        merged = dict(dotenv_values)
        merged.update(dict(base_env or os.environ))
        config_source = "development_env"
        profile_exists = False
        profile_data: dict[str, Any] = {}
    else:
        loaded_profile, resolved_profile_path, profile_errors = load_production_profile(
            repo_root=repo_root,
            profile_id=profile_id,
            profile_path=profile_path,
        )
        profile = loaded_profile or _default_profile_payload(repo_root, profile_id)
        merged = _production_profile_baseline(base_env)
        merged.update(_profile_to_env(profile, repo_root=repo_root))
        config_source = "production_profile"
        profile_exists = loaded_profile is not None
        profile_data = dict(profile)

    storage_raw = (merged.get("VIOLET_STORAGE_ROOT") or "").strip()
    storage_root = Path(storage_raw).expanduser() if storage_raw else None
    settings_path = storage_root / "data" / "settings.json" if storage_root else repo_root / "data" / "settings.json"
    settings_json = load_settings_json(settings_path)
    settings_db = settings_json.get("database", {}) if isinstance(settings_json.get("database"), dict) else {}
    settings_port_present = "port" in settings_db and str(settings_db.get("port") or "").strip() != ""
    env_port_present = "POSTGRES_PORT" in merged and str(merged.get("POSTGRES_PORT") or "").strip() != ""
    settings_port, settings_port_valid, settings_port_error, settings_port_raw = _parse_named_port(
        "settings database port",
        settings_db.get("port"),
        explicit=settings_port_present,
    )
    env_port, env_port_valid, env_port_error, env_port_raw = _parse_named_port(
        "POSTGRES_PORT",
        merged.get("POSTGRES_PORT"),
        explicit=env_port_present,
    )
    if config_source == "production_profile":
        db_port = env_port
        db_port_raw = env_port_raw
        db_port_valid = env_port_valid
        db_port_error = env_port_error
    elif settings_port_present:
        db_port = settings_port
        db_port_raw = settings_port_raw
    elif env_port_present:
        db_port = env_port
        db_port_raw = env_port_raw
    else:
        db_port = 5432
        db_port_raw = "5432"
    if config_source != "production_profile":
        db_port_valid = settings_port_valid and env_port_valid
        db_port_error = settings_port_error or env_port_error

    if config_source == "production_profile":
        db = {
            "host": merged.get("POSTGRES_HOST") or "localhost",
            "port": db_port,
            "name": merged.get("POSTGRES_DB") or "blombooru",
            "user": merged.get("POSTGRES_USER") or "postgres",
            "password": merged.get("POSTGRES_PASSWORD") or "",
        }
    else:
        db = {
            "host": settings_db.get("host") or merged.get("POSTGRES_HOST") or "localhost",
            "port": db_port,
            "name": settings_db.get("name") or merged.get("POSTGRES_DB") or "blombooru",
            "user": settings_db.get("user") or merged.get("POSTGRES_USER") or "postgres",
            "password": settings_db.get("password") or merged.get("POSTGRES_PASSWORD") or "",
        }

    port, port_resolved, port_error, port_raw = _parse_port(merged.get("APP_PORT") or str(DEFAULT_PORT))
    accepted_raw = (
        merged.get("VIOLET_PRODUCTION_STORAGE_ROOT")
        or merged.get("VIOLET_ACCEPTED_PRODUCTION_STORAGE_ROOT")
        or ""
    ).strip()
    accepted_storage_root = Path(accepted_raw).expanduser() if accepted_raw else None

    return RuntimeConfig(
        repo_root=repo_root,
        dotenv_path=dotenv_path,
        env=merged,
        storage_root=storage_root,
        settings_path=settings_path,
        settings_json=settings_json,
        db=db,
        port=port,
        url=build_url(port),
        expected_python=Path(
            merged.get("VIOLET_PRODUCTION_PYTHON") or str(expected_venv_python(repo_root))
        ),
        accepted_storage_root=accepted_storage_root,
        port_raw=port_raw,
        port_resolved=port_resolved,
        port_error=port_error,
        db_port_raw=db_port_raw,
        db_port_valid=db_port_valid,
        db_port_error=db_port_error,
        config_source=config_source,
        profile_id=profile_id,
        profile_path=resolved_profile_path,
        profile_exists=profile_exists,
        profile_data=profile_data,
        profile_errors=list(profile_errors),
    )


def build_url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def detect_git_worktree(repo_root: Path = ROOT) -> bool | None:
    try:
        git_dir = subprocess.check_output(
            ["git", "rev-parse", "--git-dir"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        common_dir = subprocess.check_output(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return _normalize_path(Path(repo_root, git_dir).resolve()) != _normalize_path(Path(repo_root, common_dir).resolve())


def _repo_root_is_codex_worktree(repo_root: Path) -> bool:
    normalized = _normalize_path(repo_root)
    return "/.codex/worktrees/" in normalized


def is_port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def _load_state(path: Path = STATE_FILE) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_state(state: Mapping[str, Any], path: Path = STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        temp_path.write_text(json.dumps(dict(state), indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _clear_state(path: Path = STATE_FILE) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _read_start_lock(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _lock_age_seconds(payload: Mapping[str, Any] | None) -> float | None:
    created = _parse_iso_timestamp((payload or {}).get("created_at"))
    if created is None:
        return None
    return max(0.0, time.time() - created)


def _start_lock_reclaimable(path: Path, *, ttl_seconds: float = START_LOCK_TTL_SECONDS) -> tuple[bool, str]:
    payload = _read_start_lock(path)
    if not payload:
        return True, "malformed_lock_reclaimed"
    try:
        pid = int(payload.get("pid"))
    except (TypeError, ValueError):
        return True, "malformed_lock_reclaimed"
    if pid <= 0:
        return True, "malformed_lock_reclaimed"
    pid_alive = process_exists(pid)
    if not pid_alive:
        return True, "dead_pid_lock_reclaimed"
    age = _lock_age_seconds(payload)
    if age is not None and age > ttl_seconds:
        command_line = process_command_line(pid).casefold()
        if not command_line or "violet_production" not in command_line:
            return True, "expired_unverified_lock_reclaimed"
    return False, "active_start_lock"


def _acquire_start_lock(path: Path = START_LOCK_FILE) -> StartLockStatus:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    stale_reclaimed = False
    try:
        fd = os.open(path, flags)
    except FileExistsError:
        reclaimable, reason = _start_lock_reclaimable(path)
        if not reclaimable:
            return StartLockStatus(acquired=False, stale_reclaimed=False, reason=reason)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        stale_reclaimed = True
        try:
            fd = os.open(path, flags)
        except FileExistsError:
            return StartLockStatus(acquired=False, stale_reclaimed=True, reason="start_lock_race")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"pid": os.getpid(), "created_at": now_iso()}, sort_keys=True))
    return StartLockStatus(acquired=True, stale_reclaimed=stale_reclaimed)


def _release_start_lock(path: Path = START_LOCK_FILE) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _append_launcher_event(event: str, payload: Mapping[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": now_iso(),
        "event": event,
        "payload": _public_payload(dict(payload)),
    }
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")


def tail_log(lines: int = 40, path: Path = LOG_FILE) -> list[str]:
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return content[-max(0, lines) :]


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if platform.system() == "Windows":
        try:
            completed = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        output = completed.stdout or ""
        return str(pid) in output and "No tasks" not in output
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_command_line(pid: int) -> str:
    if pid <= 0:
        return ""
    if platform.system() == "Windows":
        command = (
            "Get-CimInstance Win32_Process "
            f"-Filter \"ProcessId={pid}\" | "
            "Select-Object -ExpandProperty CommandLine"
        )
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return (completed.stdout or "").strip()
    try:
        completed = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (completed.stdout or "").strip()


def _parse_windows_cim_datetime(value: Any) -> float | None:
    text = str(value or "").strip()
    if len(text) < 14:
        return None
    try:
        parsed = dt.datetime.strptime(text[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return None
    offset_text = text[21:] if len(text) > 21 else ""
    if offset_text:
        try:
            offset_minutes = int(offset_text)
            tz = dt.timezone(dt.timedelta(minutes=offset_minutes))
            parsed = parsed.replace(tzinfo=tz)
        except ValueError:
            parsed = parsed.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
    else:
        parsed = parsed.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
    return parsed.timestamp()


def process_snapshot(pid: int) -> ProcessSnapshot:
    if pid <= 0 or not process_exists(pid):
        return ProcessSnapshot(pid=pid, exists=False)
    if platform.system() == "Windows":
        command = (
            "Get-CimInstance Win32_Process "
            f"-Filter \"ProcessId={pid}\" | "
            "Select-Object ProcessId,ParentProcessId,CommandLine,ExecutablePath,CreationDate | ConvertTo-Json -Compress"
        )
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
                check=False,
            )
            payload = json.loads((completed.stdout or "").strip() or "{}")
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        if isinstance(payload, Mapping):
            return ProcessSnapshot(
                pid=pid,
                exists=True,
                command_line=str(payload.get("CommandLine") or "").strip(),
                executable_path=str(payload.get("ExecutablePath") or "").strip(),
                create_time=_parse_windows_cim_datetime(payload.get("CreationDate")),
                parent_pid=_optional_int(payload.get("ParentProcessId")),
            )
    command_line = process_command_line(pid)
    executable_path = ""
    cwd = ""
    proc_root = Path("/proc") / str(pid)
    try:
        executable_path = os.readlink(proc_root / "exe")
    except OSError:
        executable_path = ""
    try:
        cwd = os.readlink(proc_root / "cwd")
    except OSError:
        cwd = ""
    return ProcessSnapshot(
        pid=pid,
        exists=bool(command_line),
        command_line=command_line,
        executable_path=executable_path,
        cwd=cwd,
    )


def _parse_iso_timestamp(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _state_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def port_owner_pid(port: int) -> int | None:
    if not (1 <= int(port) <= 65535):
        return None
    if platform.system() == "Windows":
        command = (
            "Get-NetTCPConnection "
            f"-LocalPort {int(port)} -State Listen -ErrorAction SilentlyContinue | "
            "Select-Object -First 1 -ExpandProperty OwningProcess"
        )
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        text = (completed.stdout or "").strip().splitlines()
        if not text:
            return None
        try:
            return int(text[0].strip())
        except ValueError:
            return None
    return _port_owner_pid_posix(int(port))


def _port_owner_pid_posix(port: int) -> int | None:
    try:
        completed = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-Fp"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        completed = None
    if completed is not None:
        for line in (completed.stdout or "").splitlines():
            if line.startswith("p"):
                try:
                    return int(line[1:].strip())
                except ValueError:
                    continue

    try:
        completed = subprocess.run(
            ["ss", "-ltnp"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    needle = f":{int(port)}"
    for line in (completed.stdout or "").splitlines():
        if needle not in line:
            continue
        match = re.search(r"\bpid=(\d+)", line)
        if match:
            return int(match.group(1))
    return None


def _optional_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _process_is_descendant(pid: int, ancestor_pid: int, *, max_depth: int = 5) -> bool:
    current = pid
    seen: set[int] = set()
    for _depth in range(max_depth):
        if current <= 0 or current in seen:
            return False
        seen.add(current)
        snapshot = process_snapshot(current)
        parent_pid = snapshot.parent_pid
        if parent_pid is None:
            return False
        if parent_pid == ancestor_pid:
            return True
        current = parent_pid
    return False


def _port_owner_is_verified_child(owner_pid: int, launcher_pid: int, config: RuntimeConfig | None = None) -> tuple[bool, list[str]]:
    if platform.system() != "Windows":
        return False, ["target_port_owned_by_different_pid"]
    if not _process_is_descendant(owner_pid, launcher_pid):
        return False, ["target_port_owned_by_different_pid"]
    snapshot = process_snapshot(owner_pid)
    command_line = snapshot.command_line.strip()
    command_lower = command_line.casefold()
    reasons: list[str] = []
    if not command_line:
        reasons.append("port_owner_command_line_unavailable")
    if "run.py" not in command_lower and "backend.app.main" not in command_lower:
        reasons.append("port_owner_expected_run_py_missing")
    if "--debug" in command_lower:
        reasons.append("port_owner_debug_process_refused")
    if config is not None and snapshot.cwd:
        if _normalize_path(snapshot.cwd) != _normalize_path(config.repo_root):
            reasons.append("port_owner_cwd_mismatch")
    return not reasons, reasons


def state_pid(state: Mapping[str, Any] | None) -> int | None:
    if not state:
        return None
    try:
        return int(state.get("pid"))
    except (TypeError, ValueError):
        return None


def is_launcher_managed_state(state: Mapping[str, Any] | None, config: RuntimeConfig | None = None) -> bool:
    if not state:
        return False
    if state.get("app_name") != APP_NAME or state.get("state_version") != STATE_VERSION:
        return False
    if state.get("started_by") != "violet_production_launcher":
        return False
    if config is not None:
        if _normalize_path(state.get("repo_root")) != _normalize_path(config.repo_root):
            return False
        if int(state.get("port") or 0) != config.port:
            return False
        if str(state.get("env") or "production").strip().lower() != "production":
            return False
    return True


def verify_managed_process(state: Mapping[str, Any] | None, config: RuntimeConfig | None = None) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    pid = state_pid(state)
    if pid is None or not is_launcher_managed_state(state, config):
        return False, ["launcher_state_mismatch"]
    snapshot = process_snapshot(pid)
    if not snapshot.exists:
        return False, ["pid_not_running"]
    command_line = snapshot.command_line.strip()
    command_lower = command_line.casefold()
    if not command_line:
        reasons.append("command_line_unavailable")
    if "run.py" not in command_lower and "backend.app.main" not in command_lower:
        reasons.append("expected_run_py_missing")
    if "--debug" in command_lower:
        reasons.append("debug_process_refused")
    if config is not None:
        expected_python = _normalize_path(config.expected_python)
        executable = _normalize_path(snapshot.executable_path)
        command_normalized = _normalize_path(command_line)
        if executable:
            executable_ok = executable == expected_python or expected_python in command_normalized
        else:
            executable_ok = expected_python in command_normalized
        if not executable_ok:
            reasons.append("python_executable_unverified")
        if snapshot.cwd:
            if _normalize_path(snapshot.cwd) != _normalize_path(config.repo_root):
                reasons.append("process_cwd_mismatch")
        state_start = _parse_iso_timestamp(state.get("start_time"))
        recorded_create_time = _state_float(state.get("pid_create_time"))
        if snapshot.create_time is None:
            if platform.system() == "Windows" and recorded_create_time is not None:
                reasons.append("process_create_time_unavailable")
        elif state_start is not None and snapshot.create_time + 2.0 < state_start:
            reasons.append("pid_created_before_launcher_state")
        if recorded_create_time is not None and snapshot.create_time is not None:
            if abs(snapshot.create_time - recorded_create_time) > 2.0:
                reasons.append("pid_create_time_mismatch")
        owner = port_owner_pid(config.port)
        if is_port_open(config.port) and owner is None:
            reasons.append("target_port_owner_unavailable")
        elif owner is not None and owner != pid:
            owner_verified, owner_reasons = _port_owner_is_verified_child(owner, pid, config)
            if not owner_verified:
                reasons.extend(owner_reasons)
    return not reasons, reasons


def is_managed_process(state: Mapping[str, Any] | None, config: RuntimeConfig | None = None) -> bool:
    ok, _reasons = verify_managed_process(state, config)
    return ok


def stale_state_cleanup(config: RuntimeConfig, state_path: Path = STATE_FILE) -> tuple[bool, str | None]:
    state = _load_state(state_path)
    pid = state_pid(state)
    if not state or pid is None:
        return False, None
    if is_launcher_managed_state(state, config) and not process_exists(pid):
        _clear_state(state_path)
        _append_launcher_event("stale_state_removed", {"pid": pid, "port": config.port})
        return True, "stale_state_removed"
    return False, None


def check_database_readonly(config: RuntimeConfig) -> tuple[bool, str]:
    try:
        import psycopg2  # type: ignore
    except Exception as exc:
        return False, f"psycopg2_unavailable:{exc.__class__.__name__}"

    try:
        connection = psycopg2.connect(
            dbname=config.db["name"],
            user=config.db["user"],
            password=config.db["password"],
            host=config.db["host"],
            port=config.db["port"],
            connect_timeout=5,
            options="-c default_transaction_read_only=on -c statement_timeout=5000",
        )
        try:
            connection.set_session(readonly=True, autocommit=True)
            with connection.cursor() as cursor:
                cursor.execute("SHOW transaction_read_only")
                read_only = str(cursor.fetchone()[0]).lower()
                cursor.execute("SELECT 1")
                cursor.fetchone()
            if read_only not in {"on", "true", "1"}:
                return False, "db_connection_not_read_only"
        finally:
            connection.close()
    except Exception as exc:
        return False, f"db_unreachable:{exc.__class__.__name__}"
    return True, "db_readonly_select_1_ok"


def _settings_initialized(settings_json: Mapping[str, Any]) -> bool:
    if not settings_json:
        return False
    if settings_json.get("first_run") is True:
        return False
    return isinstance(settings_json.get("database"), Mapping)


def _settings_import_write_safe(settings_json: Mapping[str, Any]) -> bool:
    return bool(settings_json.get("secret_key"))


def _profile_automation_enabled(profile: Mapping[str, Any]) -> list[str]:
    flags = profile.get("automation_flags", {}) if isinstance(profile.get("automation_flags"), Mapping) else {}
    enabled: list[str] = []
    for profile_key, env_key in PROFILE_AUTOMATION_FLAGS.items():
        if bool(flags.get(profile_key, False)):
            enabled.append(env_key)
    return enabled


def _profile_public(config: RuntimeConfig) -> dict[str, Any]:
    db = config.db
    automation_enabled = _profile_automation_enabled(config.profile_data)
    db_user = str(db.get("user") or "").strip()
    return {
        "profile_id": config.profile_id or DEFAULT_PROFILE_ID,
        "exists": config.profile_exists,
        "profile_path_local_ignored": bool(config.profile_path and profile_file_is_local_ignored(config.profile_path, config.repo_root)),
        "config_source": config.config_source,
        "env": config.env.get("VIOLET_ENV", "development").strip().lower(),
        "app_port": config.port,
        "app_port_resolved": config.port_resolved,
        "storage_root_configured": config.storage_root is not None,
        "storage_root_status": "configured" if config.storage_root else "missing",
        "require_auth": _auth_policy_bool(config.profile_data.get("require_auth"), default=True),
        "auth_policy_configured": "require_auth" in config.profile_data,
        "db": {
            "host_configured": bool(str(db.get("host") or "").strip()),
            "port": db.get("port"),
            "port_valid": config.db_port_valid,
            "name": db.get("name"),
            "user": db_user,
            "user_configured": bool(db_user),
            "password_present": bool(str(db.get("password") or "").strip()),
            "password_value_recorded": False,
        },
        "python_configured": bool(str(config.expected_python or "").strip()),
        "python_exists": config.expected_python.is_file(),
        "safe_startup": bool(config.profile_data.get("safe_startup", False)) if config.profile_data else False,
        "automation_flags_disabled": not automation_enabled,
        "automation_flags_enabled": automation_enabled,
        "development_dotenv_required": False,
        "development_dotenv_modified": False,
        "profile_errors": list(config.profile_errors),
    }


GATE_UI_MAP: dict[str, tuple[str, str, str]] = {
    "production_profile_exists": (
        "Production Profile",
        "Production profile",
        "Create or repair the local production profile. Development .env is not used for production startup.",
    ),
    "production_profile_env": (
        "Production Profile",
        "Profile environment",
        "Production profile must declare env=production.",
    ),
    "production_profile_valid": (
        "Production Profile",
        "Profile error",
        "Production profile has an ID or JSON mismatch. Use Create / Repair Production Profile.",
    ),
    "production_profile_local_ignored": (
        "Production Profile",
        "Profile storage",
        "Production profile must live under the local ignored .local_manifests/production_launcher directory.",
    ),
    "violet_env_production": (
        "Environment",
        "Production environment",
        "Development .env is not used for production. Create or repair the production profile.",
    ),
    "debug_disabled": ("Environment", "Debug disabled", "Production startup must run with BLOMBOORU_DEBUG=false."),
    "canonical_venv_python": ("Environment", "Python runtime", "Launcher must run under the configured production Python."),
    "storage_root_explicit": ("Storage", "Storage root", "Production profile is missing storage root."),
    "production_storage_root_shape": ("Storage", "Storage safety", "Production storage root is invalid or unsafe."),
    "settings_initialized": ("Storage", "Production settings", "Production storage must contain initialized data/settings.json."),
    "settings_import_write_safe": (
        "Startup Policy",
        "Settings import safety",
        "Production settings must already contain secret_key so safe startup does not write settings before the guard.",
    ),
    "db_settings_present": ("Database", "Database profile", "Production profile must contain database host, port, name, and user."),
    "db_port_valid": ("Database", "Database port", "Database port must be an integer between 1 and 65535."),
    "db_readonly_reachable": (
        "Database",
        "Read-only DB check",
        "Database check is skipped until production profile and storage gates pass.",
    ),
    "health_schema_columns": ("Schema", "Health schema check", "Health checks required core table columns."),
    "app_port_resolved": ("Port", "App port", "APP_PORT must be an integer between 1 and 65535."),
    "target_port_available_or_managed": ("Port", "Port ownership", "Target port must be free or verified as launcher-managed."),
    "destructive_e2e_disabled": ("Safety Flags", "Destructive E2E", "Destructive E2E flags must be disabled."),
    "dangerous_dev_test_flags_disabled": ("Safety Flags", "Real E2E", "Development/test run flags must be disabled."),
    "no_startup_mutation_automation": (
        "Safety Flags",
        "Startup automation",
        "Production profile must disable startup automation flags.",
    ),
    "production_auth_policy": (
        "Safety Flags",
        "Auth policy",
        "Production profile must explicitly preserve the production auth policy.",
    ),
    "startup_write_policy_explicit": (
        "Startup Policy",
        "Write policy",
        "Safe startup blocks schema migration, cleanup, import/tagging/sync jobs, and background workers.",
    ),
    "launcher_safe_startup_mode": (
        "Startup Policy",
        "Safe startup mode",
        "Child process will set VIOLET_PRODUCTION_LAUNCHER_SAFE_STARTUP=true.",
    ),
}


CHECKLIST_GROUP_ORDER = (
    "Production Profile",
    "Environment",
    "Storage",
    "Database",
    "Schema",
    "Port",
    "Safety Flags",
    "Startup Policy",
    "Health",
)


def _gate_ui_message(gate: Gate) -> str:
    mapping = GATE_UI_MAP.get(gate.name)
    if mapping is None:
        return gate.message
    default_message = mapping[2]
    if gate.passed:
        return gate.message
    if gate.name == "db_readonly_reachable" and "skipped" in gate.message:
        return default_message
    return default_message or gate.message


def _gate_ui_status(gate: Gate) -> str:
    if gate.passed:
        return "passed"
    if not gate.hard or (gate.name == "db_readonly_reachable" and "skipped" in gate.message):
        return "warning"
    return "failed"


def checklist_from_gates(gates: list[Gate]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for gate in gates:
        group, label, _message = GATE_UI_MAP.get(gate.name, ("Startup Policy", gate.name, gate.message))
        items.append(
            {
                "group": group,
                "label": label,
                "gate": gate.name,
                "status": _gate_ui_status(gate),
                "message": _gate_ui_message(gate),
                "hard": gate.hard,
            }
        )
    group_order = {name: index for index, name in enumerate(CHECKLIST_GROUP_ORDER)}
    return sorted(items, key=lambda item: (group_order.get(str(item["group"]), 999), str(item["label"])))


def _profile_gates(config: RuntimeConfig) -> list[Gate]:
    profile = config.profile_data
    automation_enabled = _profile_automation_enabled(profile)
    return [
        Gate("production_profile_exists", config.profile_exists, "Production profile exists."),
        Gate(
            "production_profile_valid",
            not config.profile_errors,
            "Production profile has no structural or profile id errors.",
        ),
        Gate(
            "production_profile_local_ignored",
            bool(config.profile_path and profile_file_is_local_ignored(config.profile_path, config.repo_root)),
            "Production profile path is local and ignored.",
        ),
        Gate(
            "production_profile_env",
            str(profile.get("env") or "").strip().lower() == "production",
            "Production profile declares env=production.",
        ),
        Gate("app_port_resolved", config.port_resolved and 1 <= config.port <= 65535, config.port_error or "App port is valid."),
        Gate("storage_root_explicit", config.storage_root is not None, "Production profile has a storage root."),
        Gate("db_port_valid", config.db_port_valid, config.db_port_error or "DB port is valid."),
        Gate(
            "db_settings_present",
            config.db_port_valid and all(str(config.db.get(key, "")).strip() for key in ("host", "name", "user")),
            "Production profile has DB host, port, name, and user.",
        ),
        Gate(
            "launcher_safe_startup_mode",
            bool(profile.get("safe_startup", False)),
            "Production profile enables launcher safe startup.",
        ),
        Gate(
            "no_startup_mutation_automation",
            not automation_enabled,
            "Production profile disables startup automation flags.",
        ),
        Gate(
            "production_auth_policy",
            "require_auth" in profile,
            "Production profile preserves production auth policy.",
        ),
    ]


def profile_status(
    *,
    repo_root: Path = ROOT,
    profile_id: str = DEFAULT_PROFILE_ID,
    profile_path: Path | None = None,
    base_env: Mapping[str, str] | None = None,
) -> ControlResult:
    config = resolve_config(repo_root, base_env=base_env, profile_id=profile_id, profile_path=profile_path)
    gates = _profile_gates(config)
    hard_failures = [gate.name for gate in gates if gate.hard and not gate.passed]
    public_errors = list(dict.fromkeys(list(config.profile_errors) + hard_failures))
    if not config.profile_exists:
        status_text = "no_profile"
        message = "No production profile exists. Create one before running production preflight."
    elif config.profile_errors:
        status_text = "profile_error"
        message = "Production profile has an ID or JSON mismatch. Repair it before preflight."
    elif hard_failures:
        status_text = "profile_incomplete"
        message = "Production profile is incomplete."
    else:
        status_text = "ready"
        message = "Production profile is complete enough for preflight."
    data = {
        "profile": _profile_public(config),
        "checklist": checklist_from_gates(gates),
        "manual_acceptance_required_before_merge": True,
        "manual_acceptance_completed": False,
        "merge_allowed": False,
    }
    return ControlResult(
        ok=not hard_failures and config.profile_exists,
        status=status_text,
        message=message,
        data=data,
        gates=gates,
        errors=public_errors,
    )


def profile_discover(
    *,
    repo_root: Path = ROOT,
    profile_id: str = DEFAULT_PROFILE_ID,
    profile_path: Path | None = None,
) -> ControlResult:
    existing, path, errors = load_production_profile(repo_root=repo_root, profile_id=profile_id, profile_path=profile_path)
    discovered, inferred = discover_local_profile_payload(repo_root, profile_id=profile_id, existing=existing)
    data = {
        "profile_exists": existing is not None,
        "profile_path_local_ignored": profile_file_is_local_ignored(path, repo_root),
        "safe_inferred_fields": {
            "profile_id": discovered["profile_id"],
            "env": discovered["env"],
            "app_port": discovered["app_port"],
            "python_configured": bool(discovered.get("python")),
            "db_host": discovered["db"]["host"],
            "db_port": discovered["db"]["port"],
            "db_name": discovered["db"]["name"],
            "db_user": discovered["db"].get("user") or "",
            "db_user_configured": bool(discovered["db"].get("user")),
            "db_access_value_available_locally": bool(discovered["db"].get("password")),
            "require_auth": _auth_policy_bool(discovered.get("require_auth"), default=True),
            "auth_policy_configured": "require_auth" in discovered,
            "storage_root_copied_from_dotenv": False,
            "storage_root_inferred_locally": bool(discovered.get("storage_root")),
        },
        "remaining_user_selected": [] if discovered.get("storage_root") else ["storage_root"],
        "discovery_notes": [
            "Existing local .env and settings are read to bootstrap the local ignored production profile.",
            "Private values are written only to the ignored local profile and are not returned in public JSON.",
        ],
        "local_inference": {
            "settings_path_found": bool(inferred.get("settings_path_found")),
            "db_from_settings": bool(inferred.get("db_from_settings")),
            "db_from_dotenv": bool(inferred.get("db_from_dotenv")),
            "auth_policy_from": inferred.get("auth_policy_from"),
            "local_access_values_written_to_profile": False,
        },
        "profile_errors": errors,
    }
    return ControlResult(ok=True, status="discovered", message="Production profile discovery completed.", data=data)


def profile_init(
    *,
    repo_root: Path = ROOT,
    profile_id: str = DEFAULT_PROFILE_ID,
    profile_path: Path | None = None,
) -> ControlResult:
    existing, path, _errors = load_production_profile(repo_root=repo_root, profile_id=profile_id, profile_path=profile_path)
    profile, _inferred = discover_local_profile_payload(repo_root, profile_id=profile_id, existing=existing)
    write_production_profile(profile, repo_root=repo_root, profile_path=path)
    result = profile_status(repo_root=repo_root, profile_id=profile_id, profile_path=path)
    result.message = "Production profile created or repaired. Complete any remaining fields before preflight."
    return result


def profile_repair(
    *,
    repo_root: Path = ROOT,
    profile_id: str = DEFAULT_PROFILE_ID,
    profile_path: Path | None = None,
) -> ControlResult:
    existing, path, _errors = load_production_profile(repo_root=repo_root, profile_id=profile_id, profile_path=profile_path)
    profile, inferred = discover_local_profile_payload(repo_root, profile_id=profile_id, existing=existing)
    write_production_profile(profile, repo_root=repo_root, profile_path=path)
    result = profile_status(repo_root=repo_root, profile_id=profile_id, profile_path=path)
    result.message = "Production profile repaired from local evidence."
    result.data["local_inference"] = {
        "settings_path_found": bool(inferred.get("settings_path_found")),
        "db_from_settings": bool(inferred.get("db_from_settings")),
        "db_from_dotenv": bool(inferred.get("db_from_dotenv")),
        "auth_policy_from": inferred.get("auth_policy_from"),
        "local_access_values_written_to_profile": bool(inferred.get("local_access_values_written_to_profile")),
    }
    return result


def _profile_identity_update_fields(updates: Mapping[str, Any]) -> list[str]:
    fields: list[str] = []
    for key in ("repo_root", "python", "app_port", "storage_root"):
        if key in updates and updates[key] is not None:
            fields.append(key)
    db_updates = updates.get("db")
    if isinstance(db_updates, Mapping):
        for key in ("host", "port", "name", "user"):
            if key in db_updates and db_updates[key] is not None:
                fields.append(f"db.{key}")
    return fields


def profile_update(
    *,
    repo_root: Path = ROOT,
    profile_id: str = DEFAULT_PROFILE_ID,
    profile_path: Path | None = None,
    base_env: Mapping[str, str] | None = None,
    updates: Mapping[str, Any] | None = None,
    state_path: Path = STATE_FILE,
) -> ControlResult:
    existing, path, _errors = load_production_profile(repo_root=repo_root, profile_id=profile_id, profile_path=profile_path)
    updates = dict(updates or {})
    identity_fields = _profile_identity_update_fields(updates)
    if identity_fields and existing is not None:
        current_config = resolve_config(repo_root, base_env=base_env, profile_id=profile_id, profile_path=path)
        state = _load_state(state_path)
        managed_running, _reasons = verify_managed_process(state, current_config)
        if managed_running:
            return ControlResult(
                ok=False,
                status="blocked",
                message="请先停止生产服务，再修改生产配置。",
                data={
                    "profile": _profile_public(current_config),
                    "checklist": checklist_from_gates(_profile_gates(current_config)),
                    "managed_by_launcher": True,
                    "blocked_identity_fields": identity_fields,
                },
                errors=["profile_identity_update_blocked_while_running"],
            )
    if existing is None:
        profile, _inferred = discover_local_profile_payload(repo_root, profile_id=profile_id, existing=None)
    else:
        profile = _coerce_profile_payload(existing, repo_root=repo_root, profile_id=profile_id)
    for key in ("storage_root", "python", "repo_root"):
        if key in updates and updates[key] is not None:
            profile[key] = str(updates[key])
    if "app_port" in updates and updates["app_port"] is not None:
        profile["app_port"] = updates["app_port"]
    if "require_auth" in updates and updates["require_auth"] is not None:
        profile["require_auth"] = _auth_policy_bool(updates["require_auth"], default=True)
    db_updates = updates.get("db")
    if isinstance(db_updates, Mapping):
        db = dict(profile["db"])
        for key in ("host", "name", "user", "password"):
            if key in db_updates and db_updates[key] is not None:
                db[key] = str(db_updates[key])
        if "port" in db_updates and db_updates["port"] is not None:
            db["port"] = db_updates["port"]
        profile["db"] = db
    write_production_profile(profile, repo_root=repo_root, profile_path=path)
    result = profile_status(repo_root=repo_root, profile_id=profile_id, profile_path=path, base_env=base_env)
    result.message = "Production profile updated."
    return result


def _storage_root_looks_production(config: RuntimeConfig) -> tuple[bool, str]:
    storage = config.storage_root
    if storage is None:
        return False, "VIOLET_STORAGE_ROOT is not explicitly set."
    if not storage.is_absolute():
        return False, "VIOLET_STORAGE_ROOT must be an absolute path."
    normalized = _normalize_path(storage)
    repo_normalized = _normalize_path(config.repo_root)
    if normalized == repo_normalized:
        if _repo_root_is_codex_worktree(config.repo_root) or detect_git_worktree(config.repo_root) is not False:
            return False, "Storage root must not be an agent worktree or unverified git worktree."
        if config.settings_path and config.settings_path.is_file():
            return True, "Storage root is the canonical production checkout with initialized local settings."
        return False, "Canonical co-located storage requires initialized data/settings.json."
    if normalized == repo_normalized or _is_relative_to(storage, config.repo_root):
        return False, "Storage root must not be inside the code repository."
    blocked_fragments = ("/.codex/", "/worktrees/", "/tmp/", "/temp/")
    if any(fragment in normalized for fragment in blocked_fragments):
        return False, "Storage root looks like a temporary or agent-managed path."
    if "icloud" in normalized:
        return False, "Storage root must not be an iCloud/source path."
    if "test" in normalized or "fixture" in normalized:
        return False, "Storage root looks like a test or fixture path."
    if config.accepted_storage_root is not None:
        if _normalize_path(config.accepted_storage_root) != normalized:
            return False, "Storage root does not match the accepted production storage root."
    return True, "Storage root is explicit and production-shaped."


def preflight(
    *,
    repo_root: Path = ROOT,
    base_env: Mapping[str, str] | None = None,
    profile_id: str | None = None,
    profile_path: Path | None = None,
    db_check: Callable[[RuntimeConfig], tuple[bool, str]] = check_database_readonly,
    state_path: Path = STATE_FILE,
) -> ControlResult:
    config = resolve_config(repo_root, base_env=base_env, profile_id=profile_id, profile_path=profile_path)
    gates: list[Gate] = []

    def gate(name: str, passed: bool, message: str, hard: bool = True) -> None:
        gates.append(Gate(name=name, passed=passed, message=message, hard=hard))

    worktree = detect_git_worktree(repo_root)
    if config.config_source == "production_profile":
        for profile_gate in _profile_gates(config):
            if profile_gate.name in {"production_profile_exists", "production_profile_valid", "production_profile_local_ignored", "production_profile_env"}:
                gate(profile_gate.name, profile_gate.passed, profile_gate.message, profile_gate.hard)
    gate("canonical_repo_root", worktree is False and not _repo_root_is_codex_worktree(repo_root), "Running from canonical repo root, not a git worktree.")
    gate("run_py_exists", (repo_root / "run.py").is_file(), "run.py exists in the production repo root.")
    configured_root = config.env.get("VIOLET_CANONICAL_REPO_ROOT", "").strip()
    if configured_root:
        gate(
            "configured_canonical_repo_root",
            _normalize_path(configured_root) == _normalize_path(repo_root),
            "Repo root matches VIOLET_CANONICAL_REPO_ROOT.",
        )

    env_is_production = config.env.get("VIOLET_ENV", "development").strip().lower() == "production"
    env_message = (
        "Production profile sets child VIOLET_ENV=production; development .env is not used."
        if config.config_source == "production_profile"
        else "VIOLET_ENV must resolve to production."
    )
    gate("violet_env_production", env_is_production and (config.profile_exists or config.config_source != "production_profile"), env_message)
    debug_disabled = not _truthy(config.env.get("BLOMBOORU_DEBUG"))
    gate("debug_disabled", debug_disabled, "BLOMBOORU_DEBUG must not be true.")
    destructive_e2e_enabled = _truthy(config.env.get("VIOLET_ALLOW_DESTRUCTIVE_E2E"))
    gate("destructive_e2e_disabled", not destructive_e2e_enabled, "VIOLET_ALLOW_DESTRUCTIVE_E2E must be false for production launcher startup.")
    dangerous_flags = [flag for flag in DANGEROUS_PRODUCTION_FLAGS if _truthy(config.env.get(flag))]
    gate(
        "dangerous_dev_test_flags_disabled",
        not dangerous_flags,
        "Destructive E2E and real E2E flags must not be enabled for production launcher startup.",
    )
    gate(
        "startup_write_policy_explicit",
        True,
        "Normal app startup maintenance is documented and launcher safe-start mode blocks schema migration, upload cleanup, and background jobs.",
    )
    gate(
        "launcher_safe_startup_mode",
        True,
        "Child process will set VIOLET_PRODUCTION_LAUNCHER_SAFE_STARTUP=true.",
    )
    gate("run_command_no_debug", not any("--debug" in part for part in production_command(config)), "Production command does not pass --debug.")
    gate(
        "canonical_venv_python",
        _normalize_path(sys.executable) == _normalize_path(config.expected_python) and config.expected_python.is_file(),
        "Launcher must run under the canonical project venv Python.",
    )
    if config.config_source == "production_profile":
        gate("dotenv_exists", True, "Development .env is not required for production profile startup.", hard=False)
    else:
        gate("dotenv_exists", config.dotenv_path.is_file(), ".env exists and is readable.")
    gate("storage_root_explicit", config.storage_root is not None, "VIOLET_STORAGE_ROOT is explicitly set.")
    storage_ok, storage_message = _storage_root_looks_production(config)
    gate("production_storage_root_shape", storage_ok, storage_message)
    settings_initialized = config.settings_path is not None and config.settings_path.is_file() and _settings_initialized(config.settings_json)
    gate("settings_initialized", settings_initialized, "data/settings.json exists and looks initialized.")
    settings_import_safe = settings_initialized and _settings_import_write_safe(config.settings_json)
    gate(
        "settings_import_write_safe",
        settings_import_safe,
        "data/settings.json already contains secret_key, so safe startup will not write settings before the guard.",
    )
    gate("db_port_valid", config.db_port_valid, config.db_port_error or "DB port is absent/defaulted or configured as a valid integer port.")
    db_settings_present = (
        config.db_port_valid
        and all(str(config.db.get(key, "")).strip() for key in ("host", "name", "user"))
        and int(config.db.get("port") or 0) > 0
    )
    gate("db_settings_present", db_settings_present, "DB host, port, name, and user are configured.")
    if (
        env_is_production
        and debug_disabled
        and (config.profile_exists or config.config_source != "production_profile")
        and storage_ok
        and settings_initialized
        and settings_import_safe
        and db_settings_present
    ):
        db_ok, db_message = db_check(config)
    else:
        db_ok, db_message = False, "db_check_skipped_until_env_storage_settings_gates_pass"
    gate("db_readonly_reachable", db_ok, db_message)
    gate("app_port_resolved", config.port_resolved and 1 <= config.port <= 65535, config.port_error or "APP_PORT is configured or defaulted to a valid port.")

    stale_state_cleanup(config, state_path)
    state = _load_state(state_path)
    port_open = is_port_open(config.port)
    port_ok = not port_open or is_managed_process(state, config)
    gate("target_port_available_or_managed", port_ok, "Target port is free or owned by this launcher state.")
    gate("no_stale_pid_claim", state is None or state_pid(state) is None or process_exists(state_pid(state) or 0), "No stale PID claims a running process.")

    enabled_automation = [flag for flag in AUTOMATION_FLAGS if _truthy(config.env.get(flag))]
    gate(
        "no_startup_mutation_automation",
        not enabled_automation,
        "No import/tagging/localization/sync automation flags are enabled for startup.",
    )

    ok = all(gate_item.passed or not gate_item.hard for gate_item in gates)
    result = ControlResult(
        ok=ok,
        status="passed" if ok else "blocked",
        message="Production preflight passed." if ok else "Production preflight blocked startup.",
        gates=gates,
        data={
            "config_source": config.config_source,
            "profile": _profile_public(config) if config.config_source == "production_profile" else None,
            "env": config.env.get("VIOLET_ENV", "development").strip().lower(),
            "debug": _truthy(config.env.get("BLOMBOORU_DEBUG")),
            "port": config.port,
            "app_port_resolved": config.port_resolved,
            "url": config.url,
            "db_name": config.db.get("name"),
            "db_port_valid": config.db_port_valid,
            "storage_root_status": "configured" if storage_ok else "blocked",
            "state_file_local_ignored": _state_file_is_local_ignored(state_path),
            "health_endpoint": HEALTH_PATH,
            "destructive_e2e_allowed": False,
            "log_tail_in_public_json": False,
            "log_tail_redacted": True,
            "startup_write_policy": startup_write_policy_public(config),
            "checklist": checklist_from_gates(gates),
            "manual_acceptance_required_before_merge": True,
            "manual_acceptance_completed": False,
            "merge_allowed": False,
        },
        errors=[gate_item.name for gate_item in gates if gate_item.hard and not gate_item.passed],
    )
    _append_launcher_event("preflight", result.to_public_dict())
    return result


def _state_file_is_local_ignored(path: Path) -> bool:
    normalized = _normalize_path(path)
    root_normalized = _normalize_path(ROOT)
    return normalized.startswith(root_normalized + "/.local_manifests/")


def production_command(config: RuntimeConfig) -> list[str]:
    return [str(config.expected_python), "run.py"]


def _child_environment(config: RuntimeConfig) -> dict[str, str]:
    if config.config_source == "production_profile":
        env = dict(config.env)
    else:
        env = os.environ.copy()
        env.update(config.env)
    env["VIOLET_ENV"] = "production"
    env["APP_PORT"] = str(config.port)
    env["BLOMBOORU_DEBUG"] = "false"
    env[STARTUP_SAFE_MODE_ENV] = "true"
    env.setdefault(STARTUP_MAINTENANCE_APPROVAL_ENV, "false")
    for flag in AUTOMATION_FLAGS:
        env[flag] = "false"
    for flag in DANGEROUS_PRODUCTION_FLAGS:
        env[flag] = "false"
    return env


def health_matches_expected(health: Mapping[str, Any] | None) -> bool:
    if not health:
        return False
    return (
        bool(health.get("ok"))
        and health.get("app_name") == APP_NAME
        and str(health.get("env") or "").strip().lower() == "production"
        and not bool(health.get("debug"))
        and bool(health.get("db_reachable"))
        and bool(health.get("schema_compatible"))
        and bool(health.get("storage_configured"))
    )


def fetch_health(url: str, *, timeout: float = 2.0) -> dict[str, Any] | None:
    request = urllib.request.Request(url.rstrip("/") + HEALTH_PATH, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def poll_health(url: str, timeout_seconds: float = READY_TIMEOUT_SECONDS) -> dict[str, Any] | None:
    deadline = time.time() + timeout_seconds
    latest_health: dict[str, Any] | None = None
    while time.time() < deadline:
        health = fetch_health(url)
        if health:
            latest_health = health
            if health.get("ok"):
                return health
        time.sleep(0.75)
    return latest_health


def _state_belongs_to_launch(state: Mapping[str, Any] | None, config: RuntimeConfig, pid: int) -> bool:
    return state_pid(state) == pid and is_launcher_managed_state(state, config)


def _cleanup_failed_launch(
    *,
    pid: int,
    config: RuntimeConfig,
    state_path: Path,
    launched_process: subprocess.Popen[Any] | None = None,
    terminate: Callable[[int, bool], None] | None = None,
    wait: Callable[[int, float], bool] | None = None,
) -> dict[str, Any]:
    current_state = _load_state(state_path)
    cleanup: dict[str, Any] = {
        "cleanup_attempted": True,
        "cleanup_succeeded": False,
        "cleanup_state_cleared": False,
        "cleanup_refused_unknown_process": False,
        "cleanup_verified_same_child": False,
        "cleanup_reaped_child": False,
        "safe_recovery": "Use Stop only if the launcher still verifies the process; otherwise inspect the PID and port before manual taskkill.",
    }
    if not _state_belongs_to_launch(current_state, config, pid):
        cleanup["cleanup_refused_unknown_process"] = True
        cleanup["cleanup_error"] = "launcher_state_no_longer_matches_failed_launch"
        return cleanup

    if launched_process is not None and getattr(launched_process, "pid", None) != pid:
        cleanup["cleanup_refused_unknown_process"] = True
        cleanup["cleanup_error"] = "launched_process_pid_mismatch"
        return cleanup

    if launched_process is not None:
        try:
            child_status = launched_process.poll()
        except Exception:
            child_status = None
        if child_status is not None:
            try:
                launched_process.wait(timeout=0)
                cleanup["cleanup_reaped_child"] = True
            except Exception:
                pass
            _clear_state(state_path)
            cleanup["cleanup_succeeded"] = True
            cleanup["cleanup_state_cleared"] = True
            cleanup["cleanup_verified_same_child"] = True
            return cleanup
        cleanup["cleanup_verified_same_child"] = True
    else:
        verified, verification_failures = verify_managed_process(current_state, config)
        if not verified:
            cleanup["cleanup_refused_unknown_process"] = True
            cleanup["cleanup_error"] = "failed_launch_pid_identity_unverified"
            cleanup["verification_failures"] = verification_failures
            return cleanup

    terminator = terminate or (lambda target_pid, force=False: _terminate_verified_pid(target_pid, force=force))
    wait_fn = wait or wait_for_exit
    if launched_process is None and not process_exists(pid):
        _clear_state(state_path)
        cleanup["cleanup_succeeded"] = True
        cleanup["cleanup_state_cleared"] = True
        return cleanup

    try:
        terminator(pid, False)
    except Exception as exc:
        cleanup["cleanup_error"] = public_error(exc)
        return cleanup

    if launched_process is not None and wait is None:
        try:
            launched_process.wait(timeout=10.0)
            cleanup["cleanup_reaped_child"] = True
            exited = True
        except subprocess.TimeoutExpired:
            exited = False
    else:
        exited = wait_fn(pid, 10.0)

    if not exited:
        current_state = _load_state(state_path)
        if not _state_belongs_to_launch(current_state, config, pid):
            cleanup["cleanup_refused_unknown_process"] = True
            cleanup["cleanup_error"] = "launcher_state_changed_during_failed_start_cleanup"
            return cleanup
        if launched_process is None:
            verified, verification_failures = verify_managed_process(current_state, config)
            if not verified:
                cleanup["cleanup_refused_unknown_process"] = True
                cleanup["cleanup_error"] = "failed_launch_pid_identity_unverified_before_force"
                cleanup["verification_failures"] = verification_failures
                return cleanup
        try:
            terminator(pid, True)
        except Exception as exc:
            cleanup["cleanup_error"] = public_error(exc)
            return cleanup
        if launched_process is not None and wait is None:
            try:
                launched_process.wait(timeout=5.0)
                cleanup["cleanup_reaped_child"] = True
                force_exited = True
            except subprocess.TimeoutExpired:
                force_exited = False
        else:
            force_exited = wait_fn(pid, 5.0)
        if not force_exited:
            cleanup["cleanup_error"] = "failed_launch_process_still_running"
            return cleanup

    current_state = _load_state(state_path)
    if _state_belongs_to_launch(current_state, config, pid):
        _clear_state(state_path)
        cleanup["cleanup_state_cleared"] = True
    cleanup["cleanup_succeeded"] = True
    return cleanup


def start_production(
    *,
    repo_root: Path = ROOT,
    base_env: Mapping[str, str] | None = None,
    profile_id: str | None = None,
    profile_path: Path | None = None,
    state_path: Path = STATE_FILE,
    start_lock_path: Path = START_LOCK_FILE,
    _lock_already_held: bool = False,
) -> ControlResult:
    config = resolve_config(repo_root, base_env=base_env, profile_id=profile_id, profile_path=profile_path)
    lock_status = StartLockStatus(acquired=True)
    if not _lock_already_held:
        lock_status = _acquire_start_lock(start_lock_path)
        if not lock_status.acquired:
            result = ControlResult(
                ok=False,
                status="blocked",
                message="Another Start or Restart action is already in progress.",
                data={
                    "running": False,
                    "managed_by_launcher": False,
                    "port": config.port,
                    "url": config.url,
                    "stale_lock_reclaimed": False,
                    "start_lock_status": lock_status.reason or "active_start_lock",
                },
                errors=["start_already_in_progress"],
            )
            _append_launcher_event("start_refused_already_in_progress", result.to_public_dict())
            return result

    def with_lock_context(result: ControlResult) -> ControlResult:
        result.data.setdefault("stale_lock_reclaimed", lock_status.stale_reclaimed)
        return result

    try:
        existing = status(repo_root=repo_root, base_env=base_env, profile_id=profile_id, profile_path=profile_path, state_path=state_path)
        if existing.data.get("running") and existing.data.get("managed_by_launcher"):
            if existing.status == "running" and existing.data.get("health_ok") is True:
                return with_lock_context(ControlResult(
                    ok=True,
                    status=existing.status,
                    message=existing.message,
                    data=existing.data,
                ))
            return with_lock_context(ControlResult(
                ok=False,
                status="unhealthy",
                message="Existing launcher-managed production process is unhealthy. Start is blocked until health is restored or the process is stopped.",
                data=existing.data,
                errors=[str(existing.data.get("last_error") or "managed_process_unhealthy")],
            ))

        preflight_result = preflight(repo_root=repo_root, base_env=base_env, profile_id=profile_id, profile_path=profile_path, state_path=state_path)
        if not preflight_result.ok:
            return with_lock_context(preflight_result)

        command = production_command(config)
        command_text = " ".join(command).lower()
        forbidden = [token for token in FORBIDDEN_START_TOKENS if token in command_text]
        if forbidden:
            result = ControlResult(
                ok=False,
                status="blocked",
                message="Production command contains a forbidden token.",
                errors=forbidden,
            )
            _append_launcher_event("start_blocked_forbidden_command", result.to_public_dict())
            return with_lock_context(result)

        STATE_DIR.mkdir(parents=True, exist_ok=True)
        log_handle = LOG_FILE.open("a", encoding="utf-8")
        log_handle.write(f"\n=== launcher start {now_iso()} ===\n")
        log_handle.flush()

        creationflags = 0
        if platform.system() == "Windows":
            creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            process = subprocess.Popen(
                command,
                cwd=str(config.repo_root),
                env=_child_environment(config),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=creationflags,
            )
        except Exception as exc:
            log_handle.close()
            result = ControlResult(
                ok=False,
                status="error",
                message="Failed to start production server.",
                errors=[public_error(exc)],
            )
            _append_launcher_event("start_failed", result.to_public_dict())
            return with_lock_context(result)

        snapshot = process_snapshot(process.pid)
        state = {
            "state_version": STATE_VERSION,
            "app_name": APP_NAME,
            "started_by": "violet_production_launcher",
            "pid": process.pid,
            "pid_create_time": snapshot.create_time,
            "start_time": now_iso(),
            "command": command,
            "repo_root": str(config.repo_root),
            "port": config.port,
            "url": config.url,
            "env": "production",
            "debug": False,
            "startup_safe_mode": True,
        }
        _write_state(state, state_path)
        _append_launcher_event("started_process", {"pid": process.pid, "port": config.port})

        health = poll_health(config.url)
        log_handle.close()
        if health:
            verified, verification_failures = verify_managed_process(state, config)
            expected_health = health_matches_expected(health)
            if not verified or not expected_health:
                cleanup = _cleanup_failed_launch(pid=process.pid, config=config, state_path=state_path, launched_process=process)
                status_data = _status_data(config, state, health)
                status_data.update(cleanup)
                status_data["running"] = process.poll() is None and process_exists(process.pid)
                status_data["managed_by_launcher"] = bool(status_data["running"] and verified)
                status_data["verification_failures"] = verification_failures
                result = ControlResult(
                    ok=False,
                    status="unhealthy",
                    message=(
                        "Production process started, but identity or health verification failed. "
                        "The launcher attempted to stop the newly started process."
                    ),
                    data=status_data,
                    errors=(verification_failures or []) + ([] if expected_health else ["health_identity_mismatch"]) + ([] if cleanup.get("cleanup_succeeded") else ["failed_launch_cleanup_incomplete"]),
                )
                _append_launcher_event("start_unhealthy_verification_failed", result.to_public_dict())
                return with_lock_context(result)
            return with_lock_context(ControlResult(
                ok=True,
                status="running",
                message="Production server started and health check passed.",
                data=_status_data(config, state, health),
            ))

        cleanup = _cleanup_failed_launch(pid=process.pid, config=config, state_path=state_path, launched_process=process)
        return with_lock_context(ControlResult(
            ok=False,
            status="error",
            message="Production server started but did not return health before timeout. The launcher attempted to stop the newly started process.",
            data={
                "running": process.poll() is None and process_exists(process.pid),
                "managed_by_launcher": False,
                "port": config.port,
                "url": config.url,
                **cleanup,
            },
            errors=["health_timeout"] + ([] if cleanup.get("cleanup_succeeded") else ["failed_launch_cleanup_incomplete"]),
        ))
    finally:
        if not _lock_already_held and lock_status.acquired:
            _release_start_lock(start_lock_path)


def _status_data(config: RuntimeConfig, state: Mapping[str, Any] | None, health: Mapping[str, Any] | None) -> dict[str, Any]:
    pid = state_pid(state)
    managed = is_managed_process(state, config)
    running = bool(pid and managed)
    health_ok = health_matches_expected(health)
    return {
        "running": running,
        "managed_by_launcher": managed,
        "config_source": config.config_source,
        "profile": _profile_public(config) if config.config_source == "production_profile" else None,
        "port": config.port,
        "app_port_resolved": config.port_resolved,
        "url": config.url,
        "env": (health or {}).get("env") or config.env.get("VIOLET_ENV", "development").strip().lower(),
        "debug": bool((health or {}).get("debug", _truthy(config.env.get("BLOMBOORU_DEBUG")))),
        "db_name": config.db.get("name"),
        "db_port_valid": config.db_port_valid,
        "db_reachable": bool((health or {}).get("db_reachable", False)),
        "schema_compatible": bool((health or {}).get("schema_compatible", False)),
        "schema_status": (health or {}).get("schema_status"),
        "storage_configured": bool((health or {}).get("storage_configured", config.storage_root is not None)),
        "storage_root_status": "configured" if config.storage_root else "missing",
        "health_ok": health_ok,
        "last_health_check": now_iso() if health else None,
        "last_error": None if health_ok else ("health_identity_mismatch_or_unhealthy" if health else "health_unavailable"),
        "destructive_e2e_allowed": False,
        "log_tail_in_public_json": False,
        "log_tail_redacted": True,
        "startup_write_policy": startup_write_policy_public(config),
        "recent_log_tail": tail_log(20),
    }


def status(
    *,
    repo_root: Path = ROOT,
    base_env: Mapping[str, str] | None = None,
    profile_id: str | None = None,
    profile_path: Path | None = None,
    state_path: Path = STATE_FILE,
) -> ControlResult:
    config = resolve_config(repo_root, base_env=base_env, profile_id=profile_id, profile_path=profile_path)
    stale_state_cleanup(config, state_path)
    state = _load_state(state_path)
    health = fetch_health(config.url)
    data = _status_data(config, state, health)
    if not data["running"] and is_port_open(config.port):
        data["running"] = False
        data["managed_by_launcher"] = False
        data["last_error"] = "target_port_owned_by_unknown_process"
    if data["running"] and not data.get("health_ok"):
        message = "Production server process is managed, but health is unavailable or failing."
        result_status = "unhealthy"
    elif data["running"]:
        message = "Production server is running."
        result_status = "running"
    else:
        message = "Production server is stopped."
        result_status = "stopped"
    result = ControlResult(ok=True, status=result_status, message=message, data=data)
    return result


def _terminate_verified_pid(pid: int, *, force: bool = False) -> None:
    if platform.system() == "Windows":
        if force:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            return
        try:
            os.kill(pid, signal.CTRL_BREAK_EVENT)
            return
        except Exception:
            subprocess.run(["taskkill", "/PID", str(pid), "/T"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            return
    os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)


def wait_for_exit(pid: int, timeout_seconds: float) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not process_exists(pid):
            return True
        time.sleep(0.25)
    return not process_exists(pid)


def stop_production(
    *,
    repo_root: Path = ROOT,
    base_env: Mapping[str, str] | None = None,
    profile_id: str | None = None,
    profile_path: Path | None = None,
    state_path: Path = STATE_FILE,
    terminate: Callable[[int, bool], None] | None = None,
) -> ControlResult:
    config = resolve_config(repo_root, base_env=base_env, profile_id=profile_id, profile_path=profile_path)
    state = _load_state(state_path)
    pid = state_pid(state)
    if pid is None or not state:
        if is_port_open(config.port):
            result = ControlResult(
                ok=False,
                status="blocked",
                message="Target port is occupied, but no launcher state exists. Refusing to stop an unknown process.",
                data={"running": False, "managed_by_launcher": False, "port": config.port, "url": config.url},
                errors=["unknown_process_refusal"],
            )
            _append_launcher_event("stop_refused_unknown_process", result.to_public_dict())
            return result
        _clear_state(state_path)
        return ControlResult(ok=True, status="stopped", message="Production server is already stopped.", data={"running": False, "managed_by_launcher": False, "port": config.port, "url": config.url})

    verified, verification_failures = verify_managed_process(state, config)
    if not verified:
        result = ControlResult(
            ok=False,
            status="blocked",
            message="Launcher state does not confidently verify the process identity. Refusing to stop an unknown process.",
            data={
                "running": process_exists(pid),
                "managed_by_launcher": False,
                "port": config.port,
                "url": config.url,
                "verification_failures": verification_failures,
            },
            errors=["unknown_or_unverified_process_refused"],
        )
        _append_launcher_event("stop_refused_unverified_state", result.to_public_dict())
        return result

    terminator = terminate or (lambda target_pid, force=False: _terminate_verified_pid(target_pid, force=force))
    try:
        terminator(pid, False)
    except Exception as exc:
        return ControlResult(ok=False, status="error", message="Graceful stop failed.", errors=[public_error(exc)])

    if not wait_for_exit(pid, 10.0):
        verified, verification_failures = verify_managed_process(state, config)
        if not verified:
            return ControlResult(
                ok=False,
                status="blocked",
                message="Process identity changed during shutdown. Refusing force kill.",
                data={"verification_failures": verification_failures},
                errors=["unknown_or_unverified_process_refused"],
            )
        try:
            terminator(pid, True)
        except Exception as exc:
            return ControlResult(ok=False, status="error", message="Force stop failed.", errors=[public_error(exc)])
        if not wait_for_exit(pid, 5.0):
            return ControlResult(ok=False, status="error", message="Verified process did not exit after force stop.", errors=["process_still_running"])

    if is_port_open(config.port):
        _clear_state(state_path)
        return ControlResult(
            ok=False,
            status="error",
            message="Process exited but target port is still occupied. Refusing further action.",
            data={"running": False, "managed_by_launcher": False, "port": config.port, "url": config.url},
            errors=["port_still_occupied_after_stop"],
        )

    _clear_state(state_path)
    _append_launcher_event("stopped_process", {"pid": pid, "port": config.port})
    return ControlResult(ok=True, status="stopped", message="Production server stopped cleanly.", data={"running": False, "managed_by_launcher": False, "port": config.port, "url": config.url, "health_ok": False})


def restart_production(**kwargs: Any) -> ControlResult:
    start_lock_path = kwargs.pop("start_lock_path", START_LOCK_FILE)
    lock_status = _acquire_start_lock(start_lock_path)
    if not lock_status.acquired:
        repo_root = kwargs.get("repo_root", ROOT)
        base_env = kwargs.get("base_env")
        config = resolve_config(repo_root, base_env=base_env, profile_id=kwargs.get("profile_id"), profile_path=kwargs.get("profile_path"))
        result = ControlResult(
            ok=False,
            status="blocked",
            message="Another Start or Restart action is already in progress.",
            data={
                "running": False,
                "managed_by_launcher": False,
                "port": config.port,
                "url": config.url,
                "stale_lock_reclaimed": False,
                "start_lock_status": lock_status.reason or "active_start_lock",
            },
            errors=["start_already_in_progress"],
        )
        _append_launcher_event("restart_refused_already_in_progress", result.to_public_dict())
        return result
    try:
        stopped = stop_production(**kwargs)
        if not stopped.ok:
            stopped.data.setdefault("stale_lock_reclaimed", lock_status.stale_reclaimed)
            return stopped
        result = start_production(
            repo_root=kwargs.get("repo_root", ROOT),
            base_env=kwargs.get("base_env"),
            profile_id=kwargs.get("profile_id"),
            profile_path=kwargs.get("profile_path"),
            state_path=kwargs.get("state_path", STATE_FILE),
            start_lock_path=start_lock_path,
            _lock_already_held=True,
        )
        result.data.setdefault("stale_lock_reclaimed", lock_status.stale_reclaimed)
        return result
    finally:
        _release_start_lock(start_lock_path)


def open_browser_target(
    *,
    repo_root: Path = ROOT,
    base_env: Mapping[str, str] | None = None,
    profile_id: str | None = None,
    profile_path: Path | None = None,
    open_func: Callable[[str], bool] = webbrowser.open,
) -> ControlResult:
    config = resolve_config(repo_root, base_env=base_env, profile_id=profile_id, profile_path=profile_path)
    opened = open_func(config.url)
    return ControlResult(ok=bool(opened), status="opened" if opened else "error", message="Browser open requested.", data={"url": config.url, "port": config.port})


def diagnostic_summary(repo_root: Path = ROOT, *, profile_id: str | None = None, profile_path: Path | None = None) -> dict[str, Any]:
    config = resolve_config(repo_root, profile_id=profile_id, profile_path=profile_path)
    current = status(repo_root=repo_root, profile_id=profile_id, profile_path=profile_path)
    return {
        "running": bool(current.data.get("running")),
        "managed_by_launcher": bool(current.data.get("managed_by_launcher")),
        "port": config.port,
        "url": config.url,
        "env": current.data.get("env"),
        "debug": bool(current.data.get("debug")),
        "db_reachable": bool(current.data.get("db_reachable")),
        "schema_compatible": bool(current.data.get("schema_compatible")),
        "schema_status": current.data.get("schema_status"),
        "health_ok": bool(current.data.get("health_ok")),
        "app_port_resolved": config.port_resolved,
        "db_port_valid": config.db_port_valid,
        "destructive_e2e_allowed": False,
        "log_tail_in_public_json": False,
        "log_tail_redacted": True,
        "startup_write_policy": startup_write_policy_public(config),
    }


def test_database(
    *,
    repo_root: Path = ROOT,
    profile_id: str = DEFAULT_PROFILE_ID,
    profile_path: Path | None = None,
    db_check: Callable[[RuntimeConfig], tuple[bool, str]] = check_database_readonly,
) -> ControlResult:
    config = resolve_config(repo_root, profile_id=profile_id, profile_path=profile_path)
    if not config.profile_exists:
        return ControlResult(
            ok=False,
            status="profile_incomplete",
            message="Create the production profile before testing the database.",
            data={"profile": _profile_public(config), "checklist": checklist_from_gates(_profile_gates(config))},
            errors=["production_profile_exists"],
        )
    db_settings_present = config.db_port_valid and all(str(config.db.get(key, "")).strip() for key in ("host", "name", "user"))
    if not db_settings_present:
        return ControlResult(
            ok=False,
            status="profile_incomplete",
            message="Database profile is incomplete.",
            data={"profile": _profile_public(config), "checklist": checklist_from_gates(_profile_gates(config))},
            errors=["db_settings_present"],
        )
    db_ok, db_message = db_check(config)
    return ControlResult(
        ok=db_ok,
        status="passed" if db_ok else "blocked",
        message="Database read-only check passed." if db_ok else "Database read-only check failed.",
        data={
            "profile": _profile_public(config),
            "db_readonly_reachable": db_ok,
            "db_message": db_message,
        },
        errors=[] if db_ok else ["db_readonly_reachable"],
    )


def _profile_update_args_to_payload(args: argparse.Namespace) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    for attr, key in (("storage_root", "storage_root"), ("python", "python"), ("repo_root", "repo_root"), ("app_port", "app_port")):
        value = getattr(args, attr)
        if value is not None:
            updates[key] = value
    db_updates: dict[str, Any] = {}
    for attr, key in (("db_host", "host"), ("db_port", "port"), ("db_name", "name"), ("db_user", "user")):
        value = getattr(args, attr)
        if value is not None:
            db_updates[key] = value
    if db_updates:
        updates["db"] = db_updates
    return updates


def _profile_update_stdin_payload(stdin_text: str) -> tuple[dict[str, Any] | None, str | None]:
    text = stdin_text.strip()
    if not text:
        return {}, None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None, "profile_update_stdin_json_invalid"
    if not isinstance(payload, Mapping):
        return None, "profile_update_stdin_json_not_object"
    return dict(payload), None


def _add_json_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Print public-safe JSON.")


def _add_profile_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", default=DEFAULT_PROFILE_ID, help="Production profile id.")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Control the local V.I.O.L.E.T. production launcher.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "status", "start", "stop", "restart", "open-browser", "diagnostic-summary", "test-db"):
        cmd = sub.add_parser(name)
        _add_json_arg(cmd)
        _add_profile_arg(cmd)
    for name in ("profile-status", "profile-discover", "profile-init", "profile-repair"):
        cmd = sub.add_parser(name)
        _add_json_arg(cmd)
        _add_profile_arg(cmd)
    update = sub.add_parser("profile-update")
    _add_json_arg(update)
    _add_profile_arg(update)
    update.add_argument("--storage-root")
    update.add_argument("--python")
    update.add_argument("--repo-root")
    update.add_argument("--app-port")
    update.add_argument("--db-host")
    update.add_argument("--db-port")
    update.add_argument("--db-name")
    update.add_argument("--db-user")
    update.add_argument("--stdin-json", action="store_true", help="Read profile update payload from stdin JSON.")
    return parser


def _print_result(result: ControlResult, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result.to_public_dict(), indent=2, sort_keys=True))
        return
    print(f"{result.status}: {result.message}")
    if result.errors:
        print("errors: " + ", ".join(result.errors))


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    if args.command == "preflight":
        result = preflight(profile_id=args.profile)
    elif args.command == "status":
        result = status(profile_id=args.profile)
    elif args.command == "start":
        result = start_production(profile_id=args.profile)
    elif args.command == "stop":
        result = stop_production(profile_id=args.profile)
    elif args.command == "restart":
        result = restart_production(profile_id=args.profile)
    elif args.command == "open-browser":
        result = open_browser_target(profile_id=args.profile)
    elif args.command == "diagnostic-summary":
        payload = diagnostic_summary(profile_id=args.profile)
        if args.json:
            print(json.dumps(_public_payload(payload), indent=2, sort_keys=True))
        else:
            print(json.dumps(_public_payload(payload), indent=2, sort_keys=True))
        return 0
    elif args.command == "test-db":
        result = test_database(profile_id=args.profile)
    elif args.command == "profile-status":
        result = profile_status(profile_id=args.profile)
    elif args.command == "profile-discover":
        result = profile_discover(profile_id=args.profile)
    elif args.command == "profile-init":
        result = profile_init(profile_id=args.profile)
    elif args.command == "profile-repair":
        result = profile_repair(profile_id=args.profile)
    elif args.command == "profile-update":
        updates = _profile_update_args_to_payload(args)
        if args.stdin_json:
            stdin_payload, stdin_error = _profile_update_stdin_payload(sys.stdin.read())
            if stdin_error:
                result = ControlResult(
                    ok=False,
                    status="error",
                    message="Profile update payload must be valid JSON.",
                    errors=[stdin_error],
                )
                _print_result(result, json_output=args.json)
                return 1
            updates.update(stdin_payload or {})
        result = profile_update(profile_id=args.profile, updates=updates)
    else:
        raise AssertionError(args.command)

    _print_result(result, json_output=args.json)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
