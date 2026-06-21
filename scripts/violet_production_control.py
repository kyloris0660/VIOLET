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
RUN_PY = ROOT / "run.py"

APP_NAME = "V.I.O.L.E.T."
STATE_VERSION = 1
DEFAULT_PORT = 8000
HEALTH_PATH = "/api/health"
READY_TIMEOUT_SECONDS = 45.0

AUTOMATION_FLAGS = (
    "DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED",
    "AI_AUTO_TAG_AFTER_IMPORT",
    "CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT",
    "TAG_TRANSLATION_AUTO_ENABLED",
    "TAG_TRANSLATION_BACKGROUND_ENABLED",
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


def resolve_config(
    repo_root: Path = ROOT,
    *,
    base_env: Mapping[str, str] | None = None,
) -> RuntimeConfig:
    dotenv_path = repo_root / ".env"
    dotenv_values = parse_dotenv(dotenv_path)
    merged = dict(dotenv_values)
    merged.update(dict(base_env or os.environ))

    storage_raw = (merged.get("VIOLET_STORAGE_ROOT") or "").strip()
    storage_root = Path(storage_raw).expanduser() if storage_raw else None
    settings_path = storage_root / "data" / "settings.json" if storage_root else repo_root / "data" / "settings.json"
    settings_json = load_settings_json(settings_path)
    settings_db = settings_json.get("database", {}) if isinstance(settings_json.get("database"), dict) else {}

    db = {
        "host": settings_db.get("host") or merged.get("POSTGRES_HOST") or "localhost",
        "port": int(settings_db.get("port") or merged.get("POSTGRES_PORT") or 5432),
        "name": settings_db.get("name") or merged.get("POSTGRES_DB") or "blombooru",
        "user": settings_db.get("user") or merged.get("POSTGRES_USER") or "postgres",
        "password": settings_db.get("password") or merged.get("POSTGRES_PASSWORD") or "",
    }

    port = int((merged.get("APP_PORT") or str(DEFAULT_PORT)).strip())
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
    path.write_text(json.dumps(dict(state), indent=2, sort_keys=True), encoding="utf-8")


def _clear_state(path: Path = STATE_FILE) -> None:
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
    return True


def is_managed_process(state: Mapping[str, Any] | None, config: RuntimeConfig | None = None) -> bool:
    pid = state_pid(state)
    if pid is None or not is_launcher_managed_state(state, config):
        return False
    if not process_exists(pid):
        return False
    command_line = process_command_line(pid).lower()
    if not command_line:
        return False
    if "run.py" not in command_line and "backend.app.main" not in command_line:
        return False
    if "--debug" in command_line:
        return False
    return True


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


def _storage_root_looks_production(config: RuntimeConfig) -> tuple[bool, str]:
    storage = config.storage_root
    if storage is None:
        return False, "VIOLET_STORAGE_ROOT is not explicitly set."
    if not storage.is_absolute():
        return False, "VIOLET_STORAGE_ROOT must be an absolute path."
    normalized = _normalize_path(storage)
    repo_normalized = _normalize_path(config.repo_root)
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
    db_check: Callable[[RuntimeConfig], tuple[bool, str]] = check_database_readonly,
    state_path: Path = STATE_FILE,
) -> ControlResult:
    config = resolve_config(repo_root, base_env=base_env)
    gates: list[Gate] = []

    def gate(name: str, passed: bool, message: str, hard: bool = True) -> None:
        gates.append(Gate(name=name, passed=passed, message=message, hard=hard))

    worktree = detect_git_worktree(repo_root)
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
    gate("violet_env_production", env_is_production, "VIOLET_ENV must resolve to production.")
    debug_disabled = not _truthy(config.env.get("BLOMBOORU_DEBUG"))
    gate("debug_disabled", debug_disabled, "BLOMBOORU_DEBUG must not be true.")
    gate("run_command_no_debug", "--debug" not in production_command(config), "Production command does not pass --debug.")
    gate(
        "canonical_venv_python",
        _normalize_path(sys.executable) == _normalize_path(config.expected_python) and config.expected_python.is_file(),
        "Launcher must run under the canonical project venv Python.",
    )
    gate("dotenv_exists", config.dotenv_path.is_file(), ".env exists and is readable.")
    gate("storage_root_explicit", config.storage_root is not None, "VIOLET_STORAGE_ROOT is explicitly set.")
    storage_ok, storage_message = _storage_root_looks_production(config)
    gate("production_storage_root_shape", storage_ok, storage_message)
    settings_initialized = config.settings_path is not None and config.settings_path.is_file() and _settings_initialized(config.settings_json)
    gate("settings_initialized", settings_initialized, "data/settings.json exists and looks initialized.")
    db_settings_present = all(str(config.db.get(key, "")).strip() for key in ("host", "name", "user")) and int(config.db.get("port") or 0) > 0
    gate("db_settings_present", db_settings_present, "DB host, port, name, and user are configured.")
    if env_is_production and debug_disabled and storage_ok and settings_initialized and db_settings_present:
        db_ok, db_message = db_check(config)
    else:
        db_ok, db_message = False, "db_check_skipped_until_env_storage_settings_gates_pass"
    gate("db_readonly_reachable", db_ok, db_message)
    gate("app_port_resolved", 1 <= config.port <= 65535, "APP_PORT is configured or defaulted to a valid port.")

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
            "env": config.env.get("VIOLET_ENV", "development").strip().lower(),
            "debug": _truthy(config.env.get("BLOMBOORU_DEBUG")),
            "port": config.port,
            "url": config.url,
            "db_name": config.db.get("name"),
            "storage_root_status": "configured" if storage_ok else "blocked",
            "state_file_local_ignored": _state_file_is_local_ignored(state_path),
            "health_endpoint": HEALTH_PATH,
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
    env = os.environ.copy()
    env.update(config.env)
    env["VIOLET_ENV"] = "production"
    env["APP_PORT"] = str(config.port)
    env["BLOMBOORU_DEBUG"] = "false"
    for flag in AUTOMATION_FLAGS:
        env.setdefault(flag, "false")
    return env


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
    while time.time() < deadline:
        health = fetch_health(url)
        if health and health.get("ok"):
            return health
        time.sleep(0.75)
    return None


def start_production(
    *,
    repo_root: Path = ROOT,
    base_env: Mapping[str, str] | None = None,
    state_path: Path = STATE_FILE,
) -> ControlResult:
    config = resolve_config(repo_root, base_env=base_env)
    existing = status(repo_root=repo_root, base_env=base_env, state_path=state_path)
    if existing.data.get("running") and existing.data.get("managed_by_launcher"):
        return ControlResult(
            ok=True,
            status="running",
            message="Production server is already running under launcher management.",
            data=existing.data,
        )

    preflight_result = preflight(repo_root=repo_root, base_env=base_env, state_path=state_path)
    if not preflight_result.ok:
        return preflight_result

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
        return result

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
        return result

    state = {
        "state_version": STATE_VERSION,
        "app_name": APP_NAME,
        "started_by": "violet_production_launcher",
        "pid": process.pid,
        "start_time": now_iso(),
        "command": command,
        "repo_root": str(config.repo_root),
        "port": config.port,
        "url": config.url,
        "env": "production",
        "debug": False,
    }
    _write_state(state, state_path)
    _append_launcher_event("started_process", {"pid": process.pid, "port": config.port})

    health = poll_health(config.url)
    log_handle.close()
    if health:
        return ControlResult(
            ok=True,
            status="running",
            message="Production server started and health check passed.",
            data=_status_data(config, state, health),
        )

    return ControlResult(
        ok=False,
        status="error",
        message="Production server started but did not become healthy before timeout.",
        data={"running": process_exists(process.pid), "managed_by_launcher": True, "port": config.port, "url": config.url},
        errors=["health_timeout"],
    )


def _status_data(config: RuntimeConfig, state: Mapping[str, Any] | None, health: Mapping[str, Any] | None) -> dict[str, Any]:
    pid = state_pid(state)
    managed = is_managed_process(state, config)
    running = bool(pid and managed)
    return {
        "running": running,
        "managed_by_launcher": managed,
        "port": config.port,
        "url": config.url,
        "env": (health or {}).get("env") or config.env.get("VIOLET_ENV", "development").strip().lower(),
        "debug": bool((health or {}).get("debug", _truthy(config.env.get("BLOMBOORU_DEBUG")))),
        "db_name": config.db.get("name"),
        "db_reachable": bool((health or {}).get("db_reachable", False)),
        "storage_configured": bool((health or {}).get("storage_configured", config.storage_root is not None)),
        "storage_root_status": "configured" if config.storage_root else "missing",
        "health_ok": bool((health or {}).get("ok", False)),
        "last_health_check": now_iso() if health else None,
        "last_error": None if health else "health_unavailable",
        "recent_log_tail": tail_log(20),
    }


def status(
    *,
    repo_root: Path = ROOT,
    base_env: Mapping[str, str] | None = None,
    state_path: Path = STATE_FILE,
) -> ControlResult:
    config = resolve_config(repo_root, base_env=base_env)
    stale_state_cleanup(config, state_path)
    state = _load_state(state_path)
    health = fetch_health(config.url)
    data = _status_data(config, state, health)
    if not data["running"] and is_port_open(config.port):
        data["running"] = False
        data["managed_by_launcher"] = False
        data["last_error"] = "target_port_owned_by_unknown_process"
    message = "Production server is running." if data["running"] else "Production server is stopped."
    result = ControlResult(ok=True, status="running" if data["running"] else "stopped", message=message, data=data)
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
    state_path: Path = STATE_FILE,
    terminate: Callable[[int, bool], None] | None = None,
) -> ControlResult:
    config = resolve_config(repo_root, base_env=base_env)
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

    if not is_managed_process(state, config):
        result = ControlResult(
            ok=False,
            status="blocked",
            message="Launcher state does not verify the process identity. Refusing to stop.",
            data={"running": process_exists(pid), "managed_by_launcher": False, "port": config.port, "url": config.url},
            errors=["managed_process_identity_failed"],
        )
        _append_launcher_event("stop_refused_unverified_state", result.to_public_dict())
        return result

    terminator = terminate or (lambda target_pid, force=False: _terminate_verified_pid(target_pid, force=force))
    try:
        terminator(pid, False)
    except Exception as exc:
        return ControlResult(ok=False, status="error", message="Graceful stop failed.", errors=[public_error(exc)])

    if not wait_for_exit(pid, 10.0):
        if not is_managed_process(state, config):
            return ControlResult(ok=False, status="blocked", message="Process identity changed during shutdown. Refusing force kill.", errors=["identity_changed_before_force_kill"])
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
    stopped = stop_production(**kwargs)
    if not stopped.ok:
        return stopped
    return start_production(
        repo_root=kwargs.get("repo_root", ROOT),
        base_env=kwargs.get("base_env"),
        state_path=kwargs.get("state_path", STATE_FILE),
    )


def open_browser_target(
    *,
    repo_root: Path = ROOT,
    base_env: Mapping[str, str] | None = None,
    open_func: Callable[[str], bool] = webbrowser.open,
) -> ControlResult:
    config = resolve_config(repo_root, base_env=base_env)
    opened = open_func(config.url)
    return ControlResult(ok=bool(opened), status="opened" if opened else "error", message="Browser open requested.", data={"url": config.url, "port": config.port})


def diagnostic_summary(repo_root: Path = ROOT) -> dict[str, Any]:
    config = resolve_config(repo_root)
    current = status(repo_root=repo_root)
    return {
        "running": bool(current.data.get("running")),
        "managed_by_launcher": bool(current.data.get("managed_by_launcher")),
        "port": config.port,
        "url": config.url,
        "env": current.data.get("env"),
        "debug": bool(current.data.get("debug")),
        "db_reachable": bool(current.data.get("db_reachable")),
        "health_ok": bool(current.data.get("health_ok")),
    }


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Control the local V.I.O.L.E.T. production launcher.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "status", "start", "stop", "restart", "open-browser"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--json", action="store_true", help="Print public-safe JSON.")
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
        result = preflight()
    elif args.command == "status":
        result = status()
        if args.json:
            print(json.dumps(diagnostic_summary(), indent=2, sort_keys=True))
            return 0 if result.ok else 1
    elif args.command == "start":
        result = start_production()
    elif args.command == "stop":
        result = stop_production()
    elif args.command == "restart":
        result = restart_production()
    elif args.command == "open-browser":
        result = open_browser_target()
    else:
        raise AssertionError(args.command)

    _print_result(result, json_output=args.json)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
