#!/usr/bin/env python3
"""
Phase 3.1.1c — Local Full-Pipeline Smoke Validation Helper.

A safe-by-default helper that validates the full local pipeline:
  env → fixture → db → preflight → dry-run → import → clip → ai-tag
  → translate → browser → safety

Safety invariants:
  • Default mode is read-only / dry-run.  Real import / CLIP / AI-tag /
    LLM-translate require ``--execute``.
  • ``ai-tag`` and ``translate`` additionally require interactive confirmation
    unless ``--yes`` is also passed.
  • Never calls cleanup / reset / delete endpoints.
  • Never modifies VioletTestFixture files.
  • Never touches iCloud folders or the production DB ``blombooru``.
  • Never prints API keys.
  • Never auto-starts / kills server processes — only checks reachability.

Usage examples::

    python scripts/local_full_pipeline_smoke.py --step env
    python scripts/local_full_pipeline_smoke.py --step import --execute
    python scripts/local_full_pipeline_smoke.py --all --execute --yes
    python scripts/local_full_pipeline_smoke.py --all --report-out reports/local-smoke/run.md
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SAFE_STEPS: frozenset[str] = frozenset(
    {"env", "fixture", "db", "preflight", "dry-run", "browser", "safety"}
)
EXECUTE_STEPS: frozenset[str] = frozenset(
    {"import", "clip", "ai-tag", "translate"}
)
CONFIRM_STEPS: frozenset[str] = frozenset({"ai-tag", "translate"})

ALL_STEPS: tuple[str, ...] = (
    "env", "fixture", "db", "preflight", "dry-run",
    "import", "clip", "ai-tag", "translate", "browser", "safety",
)

FORBIDDEN_DB_NAMES: frozenset[str] = frozenset(
    {"blombooru", "production", "main", "postgres"}
)

DEFAULT_BASE_URL = "http://localhost:8001"
DEFAULT_FIXTURE_PATH = r"C:\Users\kyloris\Pictures\VioletTestFixture"

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif",
     ".mp4", ".webm", ".mov"}
)

# Fixture subdirectories to import (order matters for reporting)
FIXTURE_SUBDIRS: tuple[str, ...] = ("anime", "non_anime", "mixed")

# Admin credentials — same defaults used by e2e tests (auth.ts)
ADMIN_USER = "admin"
ADMIN_PASS = "admin123"

# Job polling
JOB_POLL_INTERVAL = 2          # seconds
JOB_POLL_TIMEOUT  = 300        # seconds (5 min)

# ---------------------------------------------------------------------------
# Pretty printing helpers
# ---------------------------------------------------------------------------

_BOLD  = "\033[1m"
_GREEN = "\033[92m"
_RED   = "\033[91m"
_CYAN  = "\033[96m"
_YELLOW = "\033[93m"
_RESET = "\033[0m"


def _ok(msg: str) -> str:
    return f"  {_GREEN}✓{_RESET} {msg}"


def _fail(msg: str) -> str:
    return f"  {_RED}✗{_RESET} {msg}"


def _info(msg: str) -> str:
    return f"  {_CYAN}ℹ{_RESET} {msg}"


def _warn(msg: str) -> str:
    return f"  {_YELLOW}⚠{_RESET} {msg}"


def _header(step_name: str) -> str:
    return f"\n{_BOLD}{'=' * 60}\n  Step: {step_name}\n{'=' * 60}{_RESET}\n"


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable)
# ---------------------------------------------------------------------------

def count_dir_files(dirpath: str | Path) -> int:
    """Count files (not directories) recursively under *dirpath*."""
    total = 0
    for root, _dirs, files in os.walk(dirpath):
        total += len(files)
    return total


def count_supported_files(dirpath: str | Path) -> int:
    """Count files whose extension is in SUPPORTED_EXTENSIONS."""
    total = 0
    for root, _dirs, files in os.walk(dirpath):
        for f in files:
            if Path(f).suffix.lower() in SUPPORTED_EXTENSIONS:
                total += 1
    return total


def validate_base_url(url: str) -> Tuple[bool, str]:
    """Return (ok, reason).  Only localhost / 127.0.0.1 allowed."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in ("localhost", "127.0.0.1"):
        return False, f"Host '{host}' is not localhost/127.0.0.1 — refusing."
    return True, "OK"


def validate_config_diagnostics(diag: dict) -> Tuple[bool, List[str]]:
    """Validate the config-diagnostics response.  Returns (ok, issues)."""
    issues: List[str] = []

    # VIOLET_ENV must be "test"
    env_val = diag.get("environment", {}).get("VIOLET_ENV", "")
    if env_val != "test":
        issues.append(f"VIOLET_ENV is '{env_val}', expected 'test'")

    # DB name must not be forbidden
    db_name = diag.get("database", {}).get("DB_NAME", "")
    if db_name.lower() in FORBIDDEN_DB_NAMES:
        issues.append(f"DB_NAME '{db_name}' is in the forbidden list")
    if not db_name:
        issues.append("DB_NAME is empty")

    # Storage root must contain "test" (case-insensitive)
    storage_root = diag.get("storage", {}).get("STORAGE_ROOT", "")
    if "test" not in storage_root.lower():
        issues.append(
            f"STORAGE_ROOT '{storage_root}' does not contain 'test'"
        )

    # Storage root must not be the repo root
    code_root = diag.get("storage", {}).get("CODE_ROOT", "")
    if code_root and os.path.normcase(storage_root) == os.path.normcase(code_root):
        issues.append("STORAGE_ROOT is the same as CODE_ROOT (repo root)")

    # Storage root must not be a drive root / filesystem root
    sr = Path(storage_root)
    if sr == sr.anchor or str(sr) in ("C:\\", "D:\\", "/", ""):
        issues.append(f"STORAGE_ROOT '{storage_root}' looks like a filesystem root")

    return (len(issues) == 0, issues)


def mask_sensitive(value: str, visible_chars: int = 4) -> str:
    """Mask a sensitive string, showing only the last *visible_chars*."""
    if len(value) <= visible_chars:
        return "****"
    return "*" * (len(value) - visible_chars) + value[-visible_chars:]


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

class ApiClient:
    """Thin HTTP client using only stdlib (urllib)."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token: Optional[str] = None

    # -- auth ---------------------------------------------------------------

    def login(self, username: str = ADMIN_USER, password: str = ADMIN_PASS) -> bool:
        """Login and store JWT token.  Returns True on success."""
        try:
            resp = self._raw_request(
                "POST", "/api/admin/login",
                body={"username": username, "password": password},
                auth=False,
            )
            self.token = resp.get("access_token")
            return bool(self.token)
        except Exception as exc:
            print(_fail(f"Login failed: {exc}"))
            return False

    # -- generic requests ---------------------------------------------------

    def get(self, path: str) -> Any:
        return self._raw_request("GET", path)

    def post(self, path: str, body: Optional[dict] = None) -> Any:
        return self._raw_request("POST", path, body=body)

    # -- internal -----------------------------------------------------------

    def _raw_request(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
        auth: bool = True,
    ) -> Any:
        url = self.base_url + path
        data = json.dumps(body).encode() if body else None
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
            headers["Cookie"] = "admin_mode=true"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            raise RuntimeError(
                f"HTTP {exc.code} {method} {path}: {raw[:500]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Connection failed ({method} {path}): {exc.reason}"
            ) from exc


# ---------------------------------------------------------------------------
# Job poller
# ---------------------------------------------------------------------------

def poll_job(
    client: ApiClient,
    poll_url: str,
    timeout: int = JOB_POLL_TIMEOUT,
    interval: int = JOB_POLL_INTERVAL,
) -> dict:
    """Poll a background job until terminal state or timeout."""
    deadline = time.monotonic() + timeout
    terminal = {"completed", "failed", "cancelled"}
    last_status = ""
    while time.monotonic() < deadline:
        data = client.get(poll_url)
        status = data.get("status", "unknown")
        if status != last_status:
            print(_info(f"Job status: {status}"))
            last_status = status
        if status in terminal:
            return data
        time.sleep(interval)
    raise RuntimeError(f"Job timed out after {timeout}s (last status: {last_status})")


# ---------------------------------------------------------------------------
# Snapshot (before/after comparison)
# ---------------------------------------------------------------------------

def take_snapshot(
    client: ApiClient | None,
    fixture_path: str,
    test_storage_root: str | None = None,
) -> dict:
    """Capture counts for before/after comparison."""
    snap: Dict[str, Any] = {"ts": datetime.now(timezone.utc).isoformat()}

    # Fixture file counts per subdirectory
    snap["fixture"] = {}
    for sub in FIXTURE_SUBDIRS:
        d = os.path.join(fixture_path, sub)
        if os.path.isdir(d):
            snap["fixture"][sub] = {
                "total": count_dir_files(d),
                "supported": count_supported_files(d),
            }

    # Test storage file count
    if test_storage_root and os.path.isdir(test_storage_root):
        snap["storage_files"] = count_dir_files(test_storage_root)
    else:
        snap["storage_files"] = None

    # Media stats from API (optional — server may be unreachable)
    if client:
        try:
            stats = client.get("/api/admin/media-stats")
            snap["media_stats"] = stats
        except Exception:
            snap["media_stats"] = None
    else:
        snap["media_stats"] = None

    return snap


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------

def step_env(client: ApiClient, args: argparse.Namespace) -> dict:
    """Step: Environment configuration check."""
    result: Dict[str, Any] = {"step": "env", "ok": False}

    # 1 — server reachability
    try:
        diag = client.get("/api/admin/dev/config-diagnostics")
    except RuntimeError as exc:
        err_str = str(exc)
        # Is it a connection error? Suggest how to start the server
        if "Connection failed" in err_str or "urlopen" in err_str:
            print(_fail(f"Server unreachable at {client.base_url}"))
            print(_info("Start the test server in a separate PowerShell window:"))
            print(textwrap.dedent(f"""\
                $env:VIOLET_ENV="test"
                $env:POSTGRES_DB="blombooru_test"
                $env:VIOLET_STORAGE_ROOT="C:\\Users\\kyloris\\VioletStorage\\test"
                $env:VIOLET_TEST_STORAGE_ROOT="C:\\Users\\kyloris\\VioletStorage\\test"
                $env:VIOLET_TEST_FIXTURE_PATH="{args.fixture_path}"
                $env:APP_PORT="8001"
                $env:CONTENT_CLASSIFICATION_ENABLED="true"
                $env:AI_TAGGING_ENABLED="true"
                $env:TAG_TRANSLATION_LLM_ENABLED="true"
                python run.py --debug
            """))
            result["error"] = "server_unreachable"
            return result
        # Auth error — might need login
        if "401" in err_str:
            print(_fail("Authentication required.  Attempting login…"))
            if not client.login():
                result["error"] = "login_failed"
                return result
            diag = client.get("/api/admin/dev/config-diagnostics")
        else:
            print(_fail(f"Unexpected error: {err_str}"))
            result["error"] = err_str
            return result

    # 2 — validate diagnostics
    ok, issues = validate_config_diagnostics(diag)
    result["diagnostics"] = diag
    if not ok:
        print(_fail("Config diagnostics validation failed:"))
        for iss in issues:
            print(f"      - {iss}")
        result["issues"] = issues
        return result

    # 3 — report key facts (never print secrets)
    env_sec = diag.get("environment", {})
    db_sec  = diag.get("database", {})
    sto_sec = diag.get("storage", {})
    srv_sec = diag.get("server", {})
    print(_ok(f"VIOLET_ENV       = {env_sec.get('VIOLET_ENV')}"))
    print(_ok(f"DB_NAME          = {db_sec.get('DB_NAME')}"))
    print(_ok(f"STORAGE_ROOT     = {sto_sec.get('STORAGE_ROOT')}"))
    print(_ok(f"app_version      = {srv_sec.get('app_version')}"))
    print(_ok(f"python_version   = {srv_sec.get('python_version', '').split()[0]}"))

    # AI tagging config
    ai_sec = diag.get("ai_tagging", {})
    print(_info(f"AI_TAGGING       = enabled={ai_sec.get('enabled')}, "
                f"model={ai_sec.get('model_name')}"))

    # Content classification config
    cc_sec = diag.get("content_classification", {})
    print(_info(f"CONTENT_CLASS    = enabled={cc_sec.get('enabled')}, "
                f"method={cc_sec.get('method')}"))

    # Tag localization config (mask api key presence)
    tl_sec = diag.get("tag_localization", {})
    print(_info(f"TAG_LOCALIZATION = llm_enabled={tl_sec.get('llm_enabled')}, "
                f"provider={tl_sec.get('provider')}, "
                f"api_key_configured={tl_sec.get('api_key_configured')}"))

    result["ok"] = True
    return result


def step_fixture(args: argparse.Namespace) -> dict:
    """Step: Test fixture integrity check."""
    result: Dict[str, Any] = {"step": "fixture", "ok": False, "subdirs": {}}
    fixture_root = Path(args.fixture_path)

    if not fixture_root.is_dir():
        print(_fail(f"Fixture root not found: {fixture_root}"))
        result["error"] = "fixture_root_missing"
        return result

    total_all = 0
    total_supported = 0
    for sub in FIXTURE_SUBDIRS:
        d = fixture_root / sub
        if not d.is_dir():
            print(_fail(f"Subdirectory missing: {d}"))
            result["subdirs"][sub] = {"exists": False}
            continue
        tot = count_dir_files(str(d))
        sup = count_supported_files(str(d))
        unsup = tot - sup
        result["subdirs"][sub] = {
            "exists": True,
            "total": tot,
            "supported": sup,
            "unsupported": unsup,
        }
        print(_ok(f"{sub}: {tot} files ({sup} supported, {unsup} unsupported)"))
        total_all += tot
        total_supported += sup

    result["total_files"] = total_all
    result["total_supported"] = total_supported
    result["total_unsupported"] = total_all - total_supported
    print(_info(f"Total: {total_all} files, {total_supported} supported, "
                f"{total_all - total_supported} unsupported"))
    result["ok"] = all(
        s.get("exists") for s in result["subdirs"].values()
    )
    return result


def step_db(args: argparse.Namespace) -> dict:
    """Step: Test DB readiness check (via setup_test_db.py --dry-run)."""
    result: Dict[str, Any] = {"step": "db", "ok": False}

    # Safety: refuse if POSTGRES_DB env is a forbidden name
    db_name = os.environ.get("POSTGRES_DB", "")
    if db_name.lower() in FORBIDDEN_DB_NAMES:
        print(_fail(f"POSTGRES_DB='{db_name}' is a forbidden production name!"))
        result["error"] = "forbidden_db_name"
        return result

    # Find setup_test_db.py relative to this script
    script_dir = Path(__file__).resolve().parent
    setup_script = script_dir / "setup_test_db.py"
    if not setup_script.exists():
        print(_fail(f"setup_test_db.py not found at {setup_script}"))
        result["error"] = "script_not_found"
        return result

    try:
        proc = subprocess.run(
            [sys.executable, str(setup_script), "--dry-run"],
            capture_output=True, text=True, timeout=30,
        )
        print(_info(f"stdout: {proc.stdout.strip()}"))
        if proc.stderr.strip():
            print(_warn(f"stderr: {proc.stderr.strip()}"))
        result["returncode"] = proc.returncode
        result["stdout"] = proc.stdout.strip()
        result["ok"] = proc.returncode == 0
        if result["ok"]:
            print(_ok("setup_test_db.py --dry-run passed"))
        else:
            print(_fail(f"setup_test_db.py exited with code {proc.returncode}"))
    except subprocess.TimeoutExpired:
        print(_fail("setup_test_db.py timed out"))
        result["error"] = "timeout"
    except Exception as exc:
        print(_fail(f"Failed to run setup_test_db.py: {exc}"))
        result["error"] = str(exc)

    return result


def step_preflight(client: ApiClient, args: argparse.Namespace) -> dict:
    """Step: Preflight scan for each fixture subdirectory."""
    result: Dict[str, Any] = {"step": "preflight", "ok": True, "subdirs": {}}
    fixture_root = args.fixture_path

    for sub in FIXTURE_SUBDIRS:
        sub_path = os.path.join(fixture_root, sub)
        if not os.path.isdir(sub_path):
            print(_warn(f"Skipping preflight for missing dir: {sub}"))
            result["subdirs"][sub] = {"skipped": True}
            continue

        print(_info(f"Preflight: {sub} → {sub_path}"))
        try:
            resp = client.post(
                "/api/admin/scan-local-library/preflight",
                body={"paths": [sub_path]},
            )
            # Preflight creates a background job; poll it
            job_id = resp.get("id")
            if job_id:
                job = poll_job(client, f"/api/admin/scan-local-library/jobs/{job_id}")
            else:
                job = resp
            result["subdirs"][sub] = job
            ts = job.get("total_seen", 0)
            proc = job.get("processed", 0)
            status = job.get("status", "unknown")
            print(_ok(f"{sub}: status={status}, total_seen={ts}, processed={proc}"))
            if status == "failed":
                print(_fail(f"  error: {job.get('error_message', 'N/A')}"))
                result["ok"] = False
        except Exception as exc:
            print(_fail(f"Preflight failed for {sub}: {exc}"))
            result["subdirs"][sub] = {"error": str(exc)}
            result["ok"] = False

    return result


def step_dryrun(client: ApiClient, args: argparse.Namespace) -> dict:
    """Step: Dry-run import for each fixture subdirectory."""
    result: Dict[str, Any] = {"step": "dry-run", "ok": True, "subdirs": {}}
    fixture_root = args.fixture_path

    for sub in FIXTURE_SUBDIRS:
        sub_path = os.path.join(fixture_root, sub)
        if not os.path.isdir(sub_path):
            print(_warn(f"Skipping dry-run for missing dir: {sub}"))
            result["subdirs"][sub] = {"skipped": True}
            continue

        print(_info(f"Dry-run import: {sub}"))
        try:
            resp = client.post(
                "/api/admin/scan-local-library/jobs",
                body={
                    "paths": [sub_path],
                    "max_files": 50,
                    "dry_run": True,
                },
            )
            job_id = resp.get("id")
            if job_id:
                job = poll_job(client, f"/api/admin/scan-local-library/jobs/{job_id}")
            else:
                job = resp
            result["subdirs"][sub] = job
            status = job.get("status", "unknown")
            ts = job.get("total_seen", 0)
            dup = job.get("skipped_duplicate", 0)
            unsup = job.get("skipped_unsupported", 0)
            print(_ok(f"{sub}: status={status}, total_seen={ts}, "
                       f"skipped_dup={dup}, skipped_unsupported={unsup}"))
            if status == "failed":
                print(_fail(f"  error: {job.get('error_message', 'N/A')}"))
                result["ok"] = False
        except Exception as exc:
            print(_fail(f"Dry-run import failed for {sub}: {exc}"))
            result["subdirs"][sub] = {"error": str(exc)}
            result["ok"] = False

    return result


def step_import(client: ApiClient, args: argparse.Namespace) -> dict:
    """Step: Real import for each fixture subdirectory (requires --execute)."""
    result: Dict[str, Any] = {"step": "import", "ok": True, "subdirs": {}}
    fixture_root = args.fixture_path

    for sub in FIXTURE_SUBDIRS:
        sub_path = os.path.join(fixture_root, sub)
        if not os.path.isdir(sub_path):
            print(_warn(f"Skipping import for missing dir: {sub}"))
            result["subdirs"][sub] = {"skipped": True}
            continue

        print(_info(f"Real import: {sub}"))
        try:
            resp = client.post(
                "/api/admin/scan-local-library/jobs",
                body={
                    "paths": [sub_path],
                    "max_files": 50,
                    "dry_run": False,
                },
            )
            job_id = resp.get("id")
            if job_id:
                job = poll_job(client, f"/api/admin/scan-local-library/jobs/{job_id}")
            else:
                job = resp
            result["subdirs"][sub] = job
            status = job.get("status", "unknown")
            imported = job.get("imported", 0)
            dup = job.get("skipped_duplicate", 0)
            unsup = job.get("skipped_unsupported", 0)
            failed = job.get("failed", 0)
            print(_ok(f"{sub}: status={status}, imported={imported}, "
                       f"dup={dup}, unsupported={unsup}, failed={failed}"))
            if status == "failed":
                print(_fail(f"  error: {job.get('error_message', 'N/A')}"))
                result["ok"] = False
            if failed > 0:
                for ff in job.get("failed_files", [])[:5]:
                    print(_warn(f"  failed: {ff}"))
        except Exception as exc:
            print(_fail(f"Import failed for {sub}: {exc}"))
            result["subdirs"][sub] = {"error": str(exc)}
            result["ok"] = False

    return result


def step_clip(client: ApiClient, args: argparse.Namespace) -> dict:
    """Step: CLIP zero-shot content classification (requires --execute)."""
    result: Dict[str, Any] = {"step": "clip", "ok": False}

    # Check classification config
    try:
        cfg = client.get("/api/admin/content-classification/config")
        if not cfg.get("enabled"):
            print(_warn("Content classification is disabled in server config."))
            result["warning"] = "disabled"
    except Exception as exc:
        print(_warn(f"Could not read classification config: {exc}"))

    # Create classification job
    print(_info("Creating content classification job (only_unclassified=true)…"))
    try:
        resp = client.post(
            "/api/admin/content-classification/jobs",
            body={
                "max_items": 100,
                "only_unclassified": True,
            },
        )
        job_id = resp.get("id")
        if not job_id:
            print(_fail(f"No job ID returned: {resp}"))
            result["error"] = "no_job_id"
            return result

        job = poll_job(
            client,
            f"/api/admin/content-classification/jobs/{job_id}",
            timeout=600,
        )
        result["job"] = job
        status = job.get("status", "unknown")
        anime = job.get("classified_anime", 0)
        non_anime = job.get("classified_non_anime", 0)
        unknown = job.get("classified_unknown", 0)
        processed = job.get("processed", 0)
        failed = job.get("failed", 0)
        print(_ok(f"status={status}, processed={processed}"))
        print(_info(f"  anime={anime}, non_anime={non_anime}, unknown={unknown}, "
                     f"failed={failed}"))
        result["ok"] = status == "completed"
        if status == "failed":
            print(_fail(f"  error: {job.get('error_message', 'N/A')}"))
    except Exception as exc:
        print(_fail(f"CLIP classification failed: {exc}"))
        result["error"] = str(exc)

    return result


def step_aitag(client: ApiClient, args: argparse.Namespace) -> dict:
    """Step: AI auto-tagging (requires --execute + confirmation)."""
    result: Dict[str, Any] = {"step": "ai-tag", "ok": False}

    # Check model status
    try:
        ms = client.get("/api/admin/ai-tagging/model-status")
        print(_info(f"Model status: {json.dumps(ms, indent=2)[:300]}"))
    except Exception as exc:
        print(_warn(f"Could not read model status: {exc}"))

    # Dry-run first (always)
    print(_info("Dry-run AI tagging (max_items=10)…"))
    try:
        resp = client.post(
            "/api/admin/ai-tagging/jobs",
            body={
                "max_items": 10,
                "dry_run": True,
                "only_without_ai_tags": True,
            },
        )
        job_id = resp.get("id")
        if job_id:
            dry_job = poll_job(client, f"/api/admin/ai-tagging/jobs/{job_id}")
            result["dry_run"] = dry_job
            print(_ok(f"Dry-run: status={dry_job.get('status')}, "
                       f"processed={dry_job.get('processed', 0)}"))
        else:
            result["dry_run"] = resp
            print(_info(f"Dry-run response (no job): {json.dumps(resp)[:200]}"))
    except Exception as exc:
        print(_fail(f"AI-tag dry-run failed: {exc}"))
        result["error"] = str(exc)
        return result

    # Real tagging
    print(_info("Real AI tagging (max_items=10)…"))
    try:
        resp = client.post(
            "/api/admin/ai-tagging/jobs",
            body={
                "max_items": 10,
                "dry_run": False,
                "only_without_ai_tags": True,
            },
        )
        job_id = resp.get("id")
        if not job_id:
            print(_fail(f"No job ID returned: {resp}"))
            result["error"] = "no_job_id"
            return result

        job = poll_job(
            client,
            f"/api/admin/ai-tagging/jobs/{job_id}",
            timeout=600,
        )
        result["job"] = job
        status = job.get("status", "unknown")
        processed = job.get("processed", 0)
        tags_added = job.get("tags_added", 0)
        sugg_added = job.get("suggestions_added", 0)
        failed = job.get("failed", 0)
        print(_ok(f"status={status}, processed={processed}, "
                   f"tags_added={tags_added}, suggestions={sugg_added}, "
                   f"failed={failed}"))
        result["ok"] = status == "completed"
        if status == "failed":
            print(_fail(f"  error: {job.get('error_message', 'N/A')}"))
    except Exception as exc:
        print(_fail(f"AI tagging failed: {exc}"))
        result["error"] = str(exc)

    return result


def step_translate(client: ApiClient, args: argparse.Namespace) -> dict:
    """Step: LLM tag localization (requires --execute + confirmation)."""
    result: Dict[str, Any] = {"step": "translate", "ok": False}

    # LLM status check
    try:
        llm_st = client.get("/api/admin/tag-localization/llm-status")
        print(_info(f"LLM status: enabled={llm_st.get('enabled')}, "
                     f"provider={llm_st.get('provider')}, "
                     f"model={llm_st.get('model')}, "
                     f"api_key_configured={llm_st.get('api_key_configured')}"))
        result["llm_status"] = llm_st
    except Exception as exc:
        print(_fail(f"Could not read LLM status: {exc}"))
        result["error"] = str(exc)
        return result

    # Test LLM connectivity
    print(_info("Testing LLM connectivity…"))
    try:
        test_resp = client.post("/api/admin/tag-localization/test-llm")
        success = test_resp.get("success", False)
        if success:
            r = test_resp.get("result", {})
            print(_ok(f"LLM test passed: '{r.get('canonical_name')}' → "
                       f"'{r.get('display_name_zh')}'"))
        else:
            print(_fail(f"LLM test failed: {test_resp}"))
            result["error"] = "llm_test_failed"
            return result
        result["llm_test"] = test_resp
    except Exception as exc:
        print(_fail(f"LLM connectivity test failed: {exc}"))
        result["error"] = str(exc)
        return result

    # Dry-run batch translate
    print(_info("Dry-run batch translate (max_items=20)…"))
    try:
        dry_resp = client.post(
            "/api/admin/tag-localization/batch-translate",
            body={
                "dry_run": True,
                "max_items": 20,
                "language": "zh-CN",
            },
        )
        result["dry_run"] = dry_resp
        print(_ok(f"Dry-run translate: {json.dumps(dry_resp)[:300]}"))
    except Exception as exc:
        print(_fail(f"Dry-run translate failed: {exc}"))
        result["error"] = str(exc)
        return result

    # Real batch translate
    print(_info("Real batch translate (max_items=20)…"))
    try:
        real_resp = client.post(
            "/api/admin/tag-localization/batch-translate",
            body={
                "dry_run": False,
                "max_items": 20,
                "language": "zh-CN",
            },
        )
        result["batch_translate"] = real_resp
        print(_ok(f"Batch translate result: {json.dumps(real_resp)[:300]}"))
        result["ok"] = True
    except Exception as exc:
        print(_fail(f"Batch translate failed: {exc}"))
        result["error"] = str(exc)

    return result


def step_browser(args: argparse.Namespace) -> dict:
    """Step: Print browser validation commands and checklist."""
    result: Dict[str, Any] = {"step": "browser", "ok": True}
    base = args.base_url

    print(_info("Browser validation (manual or Playwright):"))
    print()
    print("  Manual checklist:")
    print(f"    1. Open {base} in Edge — gallery grid loads, thumbnails visible")
    print(f"    2. Open {base}/admin — admin panel accessible")
    print(f"    3. Click a media item — detail page shows tags, CLIP class")
    print(f"    4. Check tag localization — Chinese display names visible")
    print(f"    5. Search by tag — returns relevant results")
    print()
    print("  Playwright commands:")
    print(textwrap.dedent(f"""\
        $env:VIOLET_RUN_REAL_E2E="1"
        $env:PLAYWRIGHT_BASE_URL="{base}"
        npx playwright test tests/e2e/gallery-browse.spec.ts --project=edge
    """))

    result["base_url"] = base
    return result


def step_safety(
    client: ApiClient | None,
    args: argparse.Namespace,
    before_snap: dict | None,
    after_snap: dict | None,
) -> dict:
    """Step: Safety regression checks."""
    result: Dict[str, Any] = {"step": "safety", "ok": True, "checks": []}

    def _check(ok: bool, msg: str) -> None:
        result["checks"].append({"ok": ok, "msg": msg})
        print(_ok(msg) if ok else _fail(msg))
        if not ok:
            result["ok"] = False

    # 1 — Fixture unchanged
    if before_snap and after_snap:
        for sub in FIXTURE_SUBDIRS:
            b = before_snap.get("fixture", {}).get(sub, {}).get("total")
            a = after_snap.get("fixture", {}).get(sub, {}).get("total")
            if b is not None and a is not None:
                _check(b == a, f"Fixture/{sub} file count: before={b}, after={a}")
            else:
                _check(True, f"Fixture/{sub} count: before={b}, after={a} (N/A)")
    else:
        print(_info("No before/after snapshots available — skipping fixture diff."))

    # 2 — Config diagnostics (if server reachable)
    if client:
        try:
            diag = client.get("/api/admin/dev/config-diagnostics")
            ok, issues = validate_config_diagnostics(diag)
            _check(ok, f"Config diagnostics valid ({', '.join(issues) if issues else 'all clear'})")
        except Exception as exc:
            print(_warn(f"Could not read config diagnostics: {exc}"))

    # 3 — No iCloud paths in local library
    if client:
        try:
            diag = client.get("/api/admin/dev/config-diagnostics")
            lib_paths = diag.get("paths", {}).get("local_library_paths", [])
            icloud = [p for p in lib_paths if "icloud" in p.lower() or "mobile documents" in p.lower()]
            _check(
                len(icloud) == 0,
                f"No iCloud paths in LOCAL_LIBRARY_PATHS (found {len(icloud)})"
            )
        except Exception:
            pass

    # 4 — Destructive flags not set
    allow_destructive = os.environ.get("VIOLET_ALLOW_DESTRUCTIVE_E2E", "")
    _check(
        allow_destructive != "1",
        f"VIOLET_ALLOW_DESTRUCTIVE_E2E={allow_destructive or '(not set)'}"
    )

    # 5 — Storage diff (informational)
    if before_snap and after_snap:
        sb = before_snap.get("storage_files")
        sa = after_snap.get("storage_files")
        if sb is not None and sa is not None:
            delta = sa - sb
            print(_info(f"Test storage files: before={sb}, after={sa}, delta={delta}"))

    return result


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 3.1.1c — Local Full-Pipeline Smoke Validation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Steps:
              env        — environment/config diagnostics
              fixture    — test fixture integrity
              db         — test DB readiness
              preflight  — preflight scan per subdirectory
              dry-run    — dry-run import per subdirectory
              import     — real import (requires --execute)
              clip       — CLIP classification (requires --execute)
              ai-tag     — AI auto-tagging (requires --execute + confirm)
              translate  — LLM tag translation (requires --execute + confirm)
              browser    — browser validation commands
              safety     — safety regression checks

            Examples:
              %(prog)s --step env
              %(prog)s --step import --execute
              %(prog)s --all --execute --yes
              %(prog)s --all --report-out reports/local-smoke/run.md
        """),
    )
    parser.add_argument(
        "--step", choices=ALL_STEPS,
        help="Run a single step.",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Run all steps in sequence.",
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="Allow real-write steps (import, clip, ai-tag, translate).",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="Skip interactive confirmations for ai-tag and translate.",
    )
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE_URL,
        help=f"Test server URL (default: {DEFAULT_BASE_URL}).",
    )
    parser.add_argument(
        "--fixture-path", default=DEFAULT_FIXTURE_PATH,
        help=f"Fixture root (default: {DEFAULT_FIXTURE_PATH}).",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit JSON results to stdout.",
    )
    parser.add_argument(
        "--report-out",
        help="Write markdown report to this path (directories auto-created).",
    )
    args = parser.parse_args()

    if not args.step and not args.all:
        parser.print_help()
        sys.exit(1)

    # Determine steps to run
    if args.all:
        steps = list(ALL_STEPS)
    else:
        steps = [args.step]

    # Validate base URL
    ok, reason = validate_base_url(args.base_url)
    if not ok:
        print(_fail(reason))
        sys.exit(1)

    # Gate: refuse execute-steps without --execute
    for s in steps:
        if s in EXECUTE_STEPS and not args.execute:
            print(_fail(
                f"Step '{s}' requires --execute flag.  "
                f"Without it, only safe/read-only steps are allowed."
            ))
            sys.exit(1)

    # Interactive confirmation for confirm-steps
    for s in steps:
        if s in CONFIRM_STEPS and args.execute and not args.yes:
            warnings = {
                "ai-tag": (
                    "AI tagging may download a model (~400 MB first time) "
                    "and consume significant GPU/CPU resources."
                ),
                "translate": (
                    "LLM translation will send real API requests to the "
                    "configured provider and may incur costs."
                ),
            }
            print(_warn(warnings.get(s, f"Step '{s}' will make real changes.")))
            answer = input(f"  Proceed with '{s}'? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print(_info(f"Skipping '{s}' per user choice."))
                steps.remove(s)

    # Create API client and attempt login
    client = ApiClient(args.base_url)
    needs_server = any(
        s not in ("fixture", "browser") for s in steps
    )
    logged_in = False
    if needs_server:
        try:
            # Try an unauthenticated probe first
            client.get("/api/admin/dev/config-diagnostics")
            logged_in = True  # No auth required (unlikely but handle it)
        except RuntimeError as exc:
            if "401" in str(exc):
                logged_in = client.login()
                if not logged_in:
                    print(_fail("Could not authenticate with the test server."))
                    if "env" in steps:
                        # step_env will print helpful start instructions
                        pass
                    else:
                        sys.exit(1)
            elif "Connection failed" in str(exc) or "urlopen" in str(exc):
                print(_fail(f"Server unreachable at {args.base_url}"))
                if "env" not in steps:
                    print(_info("Run with --step env to see startup instructions."))
                    sys.exit(1)
            else:
                print(_fail(f"Unexpected error probing server: {exc}"))
                if "env" not in steps:
                    sys.exit(1)

    # Env check must pass before execute steps proceed
    env_ok = True  # optimistic default for non-env steps
    results: List[dict] = []
    before_snap: Optional[dict] = None
    after_snap: Optional[dict] = None

    # Take before snapshot
    test_storage = None
    if logged_in:
        try:
            diag = client.get("/api/admin/dev/config-diagnostics")
            test_storage = diag.get("storage", {}).get("STORAGE_ROOT")
        except Exception:
            pass
    before_snap = take_snapshot(
        client if logged_in else None,
        args.fixture_path,
        test_storage,
    )

    for step_name in steps:
        print(_header(step_name))

        # Gate execute-steps on env check
        if step_name in EXECUTE_STEPS and not env_ok:
            print(_fail(
                "Skipping — env check did not pass.  "
                "Fix environment issues first."
            ))
            results.append({"step": step_name, "ok": False, "skipped": "env_check_failed"})
            continue

        if step_name == "env":
            r = step_env(client, args)
            env_ok = r.get("ok", False)
        elif step_name == "fixture":
            r = step_fixture(args)
        elif step_name == "db":
            r = step_db(args)
        elif step_name == "preflight":
            r = step_preflight(client, args)
        elif step_name == "dry-run":
            r = step_dryrun(client, args)
        elif step_name == "import":
            r = step_import(client, args)
        elif step_name == "clip":
            r = step_clip(client, args)
        elif step_name == "ai-tag":
            r = step_aitag(client, args)
        elif step_name == "translate":
            r = step_translate(client, args)
        elif step_name == "browser":
            r = step_browser(args)
        elif step_name == "safety":
            # Take after snapshot if any mutating step was run
            mutating = EXECUTE_STEPS & set(steps)
            if mutating and args.execute:
                after_snap = take_snapshot(
                    client if logged_in else None,
                    args.fixture_path,
                    test_storage,
                )
            else:
                after_snap = take_snapshot(
                    client if logged_in else None,
                    args.fixture_path,
                    test_storage,
                )
            r = step_safety(
                client if logged_in else None,
                args,
                before_snap,
                after_snap,
            )
        else:
            r = {"step": step_name, "ok": False, "error": "unknown_step"}

        results.append(r)
        step_ok = r.get("ok", False)
        print()
        print(_ok(f"Step '{step_name}' passed") if step_ok
              else _fail(f"Step '{step_name}' had issues"))

    # Summary
    print(_header("Summary"))
    passed = sum(1 for r in results if r.get("ok"))
    total = len(results)
    for r in results:
        tag = "✓" if r.get("ok") else "✗"
        print(f"  [{tag}] {r.get('step', '?')}")
    print(f"\n  {passed}/{total} steps passed.\n")

    # JSON output
    if args.json:
        print(json.dumps(results, indent=2, default=str))

    # Report file
    if args.report_out:
        _write_report(args.report_out, results, before_snap, after_snap, args)

    sys.exit(0 if passed == total else 1)


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

def _write_report(
    path: str,
    results: List[dict],
    before_snap: dict | None,
    after_snap: dict | None,
    args: argparse.Namespace,
) -> None:
    """Write a Markdown report to *path*."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines: List[str] = []
    lines.append(f"# Phase 3.1.1c Smoke Validation Report")
    lines.append(f"")
    lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"Base URL: {args.base_url}")
    lines.append(f"Fixture: {args.fixture_path}")
    lines.append(f"Execute: {args.execute}")
    lines.append(f"")

    lines.append("## Results\n")
    lines.append("| Step | Status |")
    lines.append("|------|--------|")
    for r in results:
        tag = "✓ Pass" if r.get("ok") else "✗ Fail"
        lines.append(f"| {r.get('step', '?')} | {tag} |")
    lines.append("")

    if before_snap:
        lines.append("## Before Snapshot\n")
        lines.append(f"```json\n{json.dumps(before_snap, indent=2, default=str)}\n```\n")
    if after_snap:
        lines.append("## After Snapshot\n")
        lines.append(f"```json\n{json.dumps(after_snap, indent=2, default=str)}\n```\n")

    lines.append("## Step Details\n")
    for r in results:
        lines.append(f"### {r.get('step', '?')}\n")
        # Filter out large fields to keep report readable
        display = {k: v for k, v in r.items()
                   if k not in ("diagnostics",)}
        lines.append(f"```json\n{json.dumps(display, indent=2, default=str)}\n```\n")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(_ok(f"Report written to {out_path}"))


if __name__ == "__main__":
    main()
