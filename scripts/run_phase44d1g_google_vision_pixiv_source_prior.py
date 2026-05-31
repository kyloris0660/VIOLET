"""Phase 4.4-D1G Google Vision tiny pilot and Pixiv source-prior audit.

Lifecycle: phase-scoped operational runner. It is intentionally narrow:
only the five approved media IDs are eligible for Google Vision, only
derived/resized/metadata-stripped images are uploaded, Pixiv filename source
priors are read from DB/app-managed metadata strings only, and no DB write
path exists in this runner.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable
from urllib.parse import urlparse

from PIL import Image, ImageOps
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.enums import ContentClassEnum, FileTypeEnum  # noqa: E402
from app.models import Media  # noqa: E402

PHASE = "4.4-D1G"
PROVIDER_KEY = "google_vision_web_detection"
QUERY_TYPE = "reverse_search_derived_image"
INPUT_KIND = "derived_resized_stripped_image"
TRANSFORM_POLICY_VERSION = "phase44d1g-derived-resized-stripped-v1"
APPROVED_SAMPLE_IDS = (2690, 2687, 2670, 2654, 2647)
APPROVED_SAMPLE_SET = frozenset(APPROVED_SAMPLE_IDS)
GOOGLE_PROJECT_ID = "image-project-497811"
VISION_ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"
MAX_DERIVED_DIMENSION = 768
MAX_RESULTS = 20
DERIVED_FORMAT = "JPEG"
DERIVED_MIME_TYPE = "image/jpeg"
PIXIV_PRIOR_RE = re.compile(r"(?<!\d)(?P<pixiv_work_id>[1-9]\d{5,11})_p(?P<page_index>\d+)(?!\d)")
LOCAL_DETAILS_DIR = Path(".local_manifests")
PUBLIC_REPORT_DIR = Path("docs/reports")
SOURCE_LIKE_HOST_PATTERNS = (
    "pixiv.net",
    "danbooru.donmai.us",
    "gelbooru.com",
    "safebooru.org",
    "yande.re",
    "konachan.com",
    "anime-pictures.net",
    "deviantart.com",
    "artstation.com",
    "twitter.com",
    "x.com",
    "skeb.jp",
    "zerochan.net",
)
WRITE_SQL_RE = re.compile(
    r"^\s*(insert|update|delete|merge|alter|drop|truncate|create|replace|grant|revoke|copy\s+.+\s+from|vacuum)\b",
    re.IGNORECASE | re.DOTALL,
)
LOCAL_PATH_RE = re.compile(r"(?i)([a-z]:[\\/]|\\\\|file://|/(users|home|root|mnt|volumes|workspace|tmp|var)(/|$))")
SECRET_TEXT_RE = re.compile(r"(?i)(bearer\s+[A-Za-z0-9._~+\-/]{8,}|access[_-]?token\s*[=:]|api[_-]?key\s*[=:]|sk-[A-Za-z0-9_-]{16,})")


class Phase44D1GError(RuntimeError):
    pass


class GcloudDiscoveryBlocked(Phase44D1GError):
    pass


class GooglePreflightBlocked(Phase44D1GError):
    pass


class SampleGateError(Phase44D1GError):
    pass


class EnvBlockedError(Phase44D1GError):
    pass


class IdentityBlockedError(Phase44D1GError):
    pass


class PrivacyBlocked(Phase44D1GError):
    pass


class ReadOnlyViolation(Phase44D1GError):
    pass


class OutputPathError(Phase44D1GError):
    pass


@dataclass(frozen=True)
class ProjectConfig:
    project_root: Path
    violet_env: str
    storage_root: Path
    storage_root_explicitly_set: bool
    db_user: str
    db_password: str
    db_host: str
    db_port: int
    db_name: str

    @property
    def database_url(self) -> URL:
        return URL.create(
            drivername="postgresql",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )


@dataclass(frozen=True)
class StorageCheck:
    status: str
    exists: bool
    is_file: bool
    relative_kind: str
    size_bytes: int | None = None
    dimensions: dict[str, int] | None = None


@dataclass(frozen=True)
class DerivedInput:
    media_id: int
    artifact_id: str
    safe_filename: str
    source_kind: str
    width: int
    height: int
    size_bytes: int
    sha256: str
    path: Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _enum_label(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        from dotenv import dotenv_values

        values = dotenv_values(path)
        return {str(k): str(v) for k, v in values.items() if k and v is not None}
    except Exception:
        parsed: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key, value = stripped.split("=", 1)
                parsed[key.strip()] = value.strip().strip('"').strip("'")
        return parsed


def _env_value(dotenv_values: dict[str, str], key: str, default: str = "") -> str:
    if key in os.environ:
        return os.environ.get(key, default)
    return dotenv_values.get(key, default)


def _load_file_settings(storage_root: Path) -> dict[str, Any]:
    settings_file = storage_root / "data" / "settings.json"
    if not settings_file.exists():
        return {}
    try:
        data = json.loads(settings_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IdentityBlockedError("identity_blocked: data/settings.json is not valid JSON") from exc
    return data if isinstance(data, dict) else {}


def _is_test_storage_path(path: Path) -> bool:
    normalized = str(path).replace("/", "\\").rstrip("\\").lower()
    return normalized.endswith("\\violetstorage\\test") or "\\violetstorage\\test\\" in normalized


def load_project_config(project_root: Path = ROOT) -> ProjectConfig:
    dotenv_values = _read_dotenv(project_root / ".env")
    violet_env_raw = _env_value(dotenv_values, "VIOLET_ENV", "").strip()
    violet_env = (violet_env_raw or "development").lower()
    if violet_env != "development":
        reported_env = violet_env_raw or "unset"
        raise EnvBlockedError(
            f"env_blocked: VIOLET_ENV must be 'development' for {PHASE}; got {reported_env!r}"
        )
    if _env_value(dotenv_values, "TEST_DATABASE_URL", "").strip():
        raise EnvBlockedError("env_blocked: TEST_DATABASE_URL is set; refusing development DB audit")

    storage_env = _env_value(dotenv_values, "VIOLET_STORAGE_ROOT", "").strip()
    storage_root = (Path(storage_env) if storage_env else project_root).resolve()
    if _is_test_storage_path(storage_root):
        raise IdentityBlockedError("identity_blocked: storage root points at test storage")

    file_settings = _load_file_settings(storage_root)
    db_settings = file_settings.get("database", {}) if isinstance(file_settings.get("database"), dict) else {}
    db_name = str(db_settings.get("name") or "").strip() or _env_value(dotenv_values, "POSTGRES_DB", "").strip() or "blombooru"
    if db_name == "blombooru_test":
        raise IdentityBlockedError("identity_blocked: target DB is blombooru_test, not blombooru")
    db_host = str(db_settings.get("host") or "").strip() or _env_value(dotenv_values, "POSTGRES_HOST", "").strip() or "db"
    db_port = int(str(db_settings.get("port") or "").strip() or _env_value(dotenv_values, "POSTGRES_PORT", "").strip() or "5432")
    db_user = str(db_settings.get("user") or "").strip() or _env_value(dotenv_values, "POSTGRES_USER", "").strip() or "postgres"
    db_password = str(db_settings.get("password") or "") or _env_value(dotenv_values, "POSTGRES_PASSWORD", "")
    return ProjectConfig(
        project_root=project_root.resolve(),
        violet_env=violet_env,
        storage_root=storage_root,
        storage_root_explicitly_set=bool(storage_env),
        db_user=db_user,
        db_password=db_password,
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
    )


def _path_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def resolve_output_path(raw_path: str, *, expected_parent: Path) -> Path:
    raw_text = str(raw_path)
    if raw_text.startswith("\\\\") or raw_text.startswith("//") or re.match(r"(?i)^z:[\\/]", raw_text):
        raise OutputPathError("output_path_blocked: NAS/network-share paths are not allowed")
    candidate = Path(raw_path)
    resolved = candidate.resolve() if candidate.is_absolute() else (ROOT / candidate).resolve()
    if not _path_relative_to(resolved, ROOT.resolve()):
        raise OutputPathError("output_path_blocked: output must stay under repository root")
    if not _path_relative_to(resolved, (ROOT / expected_parent).resolve()):
        raise OutputPathError(f"output_path_blocked: output must stay under {expected_parent.as_posix()}")
    return resolved


def parse_media_ids(media_ids: Iterable[int] | None) -> list[int]:
    values = list(media_ids or [])
    if not values:
        raise SampleGateError("sample_gate_blocked: explicit --media-ids are required")
    requested = list(dict.fromkeys(int(item) for item in values))
    outside = [item for item in requested if item not in APPROVED_SAMPLE_SET]
    if outside:
        raise SampleGateError("sample_gate_blocked: media IDs outside approved sample set: " + ", ".join(map(str, outside)))
    omitted = [item for item in APPROVED_SAMPLE_IDS if item not in requested]
    if omitted:
        raise SampleGateError("sample_gate_blocked: exact approved sample set required; omitted IDs: " + ", ".join(map(str, omitted)))
    return requested


def discover_gcloud(explicit_path: str | None = None) -> dict[str, Any]:
    searched = [
        "PATH:Get-Command/shutil.which",
        "LOCALAPPDATA Google Cloud SDK gcloud.cmd",
        "ProgramFiles Google Cloud SDK gcloud.cmd",
        "ProgramFiles(x86) Google Cloud SDK gcloud.cmd",
        "USERPROFILE AppData Local Google Cloud SDK gcloud.cmd",
    ]
    if explicit_path:
        candidate = Path(explicit_path)
        if candidate.exists():
            return {
                "status": "tool_found_by_absolute_path",
                "selected_path": str(candidate),
                "selected_path_category": "explicit_absolute_path",
                "searched_locations": searched,
                "path_print_safe": False,
            }
        raise GcloudDiscoveryBlocked("tool_executable_not_found")

    path_match = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    if path_match:
        return {
            "status": "tool_found_in_current_shell_path",
            "selected_path": path_match,
            "selected_path_category": "current_shell_path",
            "searched_locations": searched,
            "path_print_safe": False,
        }

    candidate_texts = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Cloud SDK", "google-cloud-sdk", "bin", "gcloud.cmd"),
        os.path.join(os.environ.get("ProgramFiles", ""), "Google", "Cloud SDK", "google-cloud-sdk", "bin", "gcloud.cmd"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Google", "Cloud SDK", "google-cloud-sdk", "bin", "gcloud.cmd"),
        os.path.join(os.environ.get("USERPROFILE", ""), "AppData", "Local", "Google", "Cloud SDK", "google-cloud-sdk", "bin", "gcloud.cmd"),
    ]
    candidates = [Path(item) for item in candidate_texts if item and Path(item).exists()]
    if not candidates:
        raise GcloudDiscoveryBlocked("tool_executable_not_found")
    preferred = Path(candidate_texts[0])
    selected = preferred if preferred in candidates else candidates[0]
    return {
        "status": "tool_found_by_absolute_path",
        "selected_path": str(selected),
        "selected_path_category": "absolute_LOCALAPPDATA_preferred" if selected == preferred else "absolute_common_location_first",
        "candidate_count": len(candidates),
        "searched_locations": searched,
        "path_print_safe": False,
    }


def _run_gcloud(gcloud_path: str, args: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [gcloud_path, *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def google_preflight(gcloud: dict[str, Any], *, project_root: Path = ROOT) -> dict[str, Any]:
    gcloud_path = str(gcloud["selected_path"])
    version = _run_gcloud(gcloud_path, ["--version"])
    if version.returncode != 0:
        raise GooglePreflightBlocked("blocked_gcloud_not_runnable")
    auth = _run_gcloud(gcloud_path, ["auth", "list", "--format=json"])
    if auth.returncode != 0:
        raise GooglePreflightBlocked("blocked_gcloud_not_runnable")
    try:
        auth_rows = json.loads(auth.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise GooglePreflightBlocked("blocked_gcloud_not_runnable") from exc
    active_count = sum(1 for row in auth_rows if isinstance(row, dict) and row.get("status") == "ACTIVE")

    project = (_run_gcloud(gcloud_path, ["config", "get-value", "project"]).stdout or "").strip()
    quota_project = (_run_gcloud(gcloud_path, ["config", "get-value", "billing/quota_project"]).stdout or "").strip()
    services = _run_gcloud(
        gcloud_path,
        [
            "services",
            "list",
            "--enabled",
            "--project",
            GOOGLE_PROJECT_ID,
            "--filter=config.name:vision.googleapis.com",
            "--format=value(config.name)",
        ],
    )
    if services.returncode != 0:
        raise GooglePreflightBlocked("blocked_google_billing_or_permission_uncertain")
    vision_enabled = "vision.googleapis.com" in (services.stdout or "")
    token = _run_gcloud(gcloud_path, ["auth", "application-default", "print-access-token"], timeout=60)
    adc_available = token.returncode == 0 and bool((token.stdout or "").strip())
    token_stdout_len = len(token.stdout or "")
    del token
    if not adc_available:
        raise GooglePreflightBlocked("blocked_google_adc_token_unavailable")

    if not (project == GOOGLE_PROJECT_ID or quota_project == GOOGLE_PROJECT_ID):
        raise GooglePreflightBlocked("blocked_google_wrong_quota_project")
    if quota_project != GOOGLE_PROJECT_ID:
        raise GooglePreflightBlocked("blocked_google_wrong_quota_project")
    if not vision_enabled:
        raise GooglePreflightBlocked("blocked_google_vision_api_not_enabled")

    repo = project_root.resolve()
    gac = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    gac_set = bool(gac)
    gac_outside_repo: bool | None = None
    if gac_set:
        try:
            gac_path = Path(gac).resolve()
        except OSError:
            gac_path = Path(gac)
        gac_outside_repo = not str(gac_path).lower().startswith(str(repo).lower())
        if not gac_outside_repo:
            raise GooglePreflightBlocked("blocked_google_credentials_inside_repo")

    return {
        "gcloud_discovery_status": gcloud["status"],
        "gcloud_path_category": gcloud["selected_path_category"],
        "gcloud_absolute_path_used": gcloud["status"] == "tool_found_by_absolute_path",
        "gcloud_version_first_line": (version.stdout or "").splitlines()[0] if (version.stdout or "").splitlines() else None,
        "active_account_present": active_count > 0,
        "active_account_count": active_count,
        "project": project,
        "project_ok": project == GOOGLE_PROJECT_ID,
        "quota_project": quota_project,
        "quota_project_ok": quota_project == GOOGLE_PROJECT_ID,
        "vision_api_enabled": vision_enabled,
        "adc_token_available": True,
        "adc_token_printed": False,
        "adc_token_stdout_length_recorded_private": token_stdout_len,
        "google_application_credentials_set": gac_set,
        "google_application_credentials_outside_repo": gac_outside_repo,
        "google_application_credentials_path_printed": False,
        "credential_contents_printed": False,
    }


def _resolve_storage_path(storage_root: Path, stored_path: str | None) -> Path | None:
    if not stored_path:
        return None
    raw = str(stored_path)
    if raw.startswith("\\\\") or raw.startswith("//"):
        return None
    normalized = raw.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized) or PureWindowsPath(raw).is_absolute():
        return None
    probe = Path(normalized)
    if probe.is_absolute() or ".." in probe.parts:
        return None
    storage_resolved = storage_root.resolve()
    resolved = (storage_resolved / normalized).resolve()
    return resolved if _path_relative_to(resolved, storage_resolved) else None


def _relative_kind(stored_path: str | None) -> str:
    normalized = (stored_path or "").replace("\\", "/")
    if normalized.startswith("media/original/"):
        return "app_managed_original"
    if normalized.startswith("media/thumbnails/"):
        return "app_managed_thumbnail"
    if normalized.startswith("media/"):
        return "app_managed_other"
    return "not_app_managed_media"


def inspect_storage_path(
    storage_root: Path,
    stored_path: str | None,
    *,
    expected_kind: str,
    read_dimensions: bool = False,
) -> StorageCheck:
    relative_kind = _relative_kind(stored_path)
    if relative_kind != expected_kind:
        return StorageCheck("unsafe_or_wrong_app_managed_kind", False, False, relative_kind)
    resolved = _resolve_storage_path(storage_root, stored_path)
    if resolved is None:
        return StorageCheck("unsafe_storage_path", False, False, relative_kind)
    try:
        exists = resolved.exists()
        is_file = resolved.is_file()
        size = resolved.stat().st_size if exists and is_file else None
    except OSError:
        return StorageCheck("unreadable_app_managed_path", False, False, relative_kind)
    dimensions = None
    status = "present" if exists and is_file else "missing"
    if exists and is_file and read_dimensions:
        try:
            with Image.open(resolved) as image:
                dimensions = {"width": int(image.width), "height": int(image.height)}
        except Exception:
            status = "unreadable_image_metadata"
    return StorageCheck(status, exists, is_file, relative_kind, size, dimensions)


def _content_class_label(value: Any) -> str:
    return _enum_label(value) or "null_unclassified"


def _file_type_label(value: Any) -> str:
    return _enum_label(value) or "unknown"


def _blocked_reason_for_media(media: Media, original: StorageCheck, thumbnail: StorageCheck) -> str | None:
    content_class = _content_class_label(media.content_class)
    if content_class != ContentClassEnum.anime.value:
        return f"blocked_by_content_class:{content_class}"
    file_type = _file_type_label(media.file_type)
    if file_type not in {FileTypeEnum.image.value, FileTypeEnum.gif.value}:
        return f"blocked_by_file_type:{file_type}"
    if original.status != "present" and thumbnail.status != "present":
        return "blocked_by_app_managed_media_unavailable"
    return None


def _request_shape(media_id: int, *, content_class: str, derived_sha256: str | None = None) -> tuple[dict[str, Any], str]:
    public_shape = {
        "phase": PHASE,
        "provider_key": PROVIDER_KEY,
        "query_type": QUERY_TYPE,
        "input_kind": INPUT_KIND,
        "input_privacy_mode": "derived_upload_approved_sample_only",
        "media_ref": f"approved_media_id:{int(media_id)}",
        "content_class": content_class,
        "transform_policy_version": TRANSFORM_POLICY_VERSION,
        "max_derived_dimension": MAX_DERIVED_DIMENSION,
        "feature_type": "WEB_DETECTION",
        "max_results": MAX_RESULTS,
        "send_original": False,
        "send_thumbnail": False,
        "send_derived": True,
        "local_path_included": False,
        "filename_included": False,
        "source_label_included": False,
    }
    hash_shape = dict(public_shape)
    if derived_sha256:
        hash_shape["derived_sha256_private"] = derived_sha256
    payload = json.dumps(hash_shape, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return public_shape, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_sample_gate(db: Session, *, media_ids: list[int], storage_root: Path) -> tuple[dict[str, Any], dict[int, Media]]:
    rows = db.query(Media).filter(Media.id.in_(media_ids)).all()
    by_id = {int(row.id): row for row in rows}
    request_plan: list[dict[str, Any]] = []
    private_rows: list[dict[str, Any]] = []
    content_distribution: Counter[str] = Counter()
    blocked_reasons: Counter[str] = Counter()
    for media_id in media_ids:
        media = by_id.get(media_id)
        if media is None:
            blocked_reasons["missing_media"] += 1
            content_distribution["missing"] += 1
            shape, query_hash = _request_shape(media_id, content_class="missing")
            request_plan.append(
                {
                    "media_id": media_id,
                    "content_class": "missing",
                    "eligibility_status": "blocked",
                    "blocked_reason": "missing_media",
                    "request_shape_redacted": shape,
                    "query_hash_status": "blocked_missing_media",
                    "query_hash": None,
                    "would_send_original": False,
                    "would_send_thumbnail": False,
                    "would_send_derived_image": False,
                    "local_path_included": False,
                    "filename_included": False,
                    "source_label_included": False,
                }
            )
            private_rows.append({"media_id": media_id, "found": False, "blocked_reason": "missing_media"})
            continue
        content_class = _content_class_label(media.content_class)
        original = inspect_storage_path(storage_root, media.path, expected_kind="app_managed_original", read_dimensions=True)
        thumbnail = inspect_storage_path(storage_root, media.thumbnail_path, expected_kind="app_managed_thumbnail", read_dimensions=True)
        reason = _blocked_reason_for_media(media, original, thumbnail)
        status = "blocked" if reason else "eligible"
        if reason:
            blocked_reasons[reason] += 1
        content_distribution[content_class] += 1
        shape, query_hash = _request_shape(media_id, content_class=content_class)
        request_plan.append(
            {
                "media_id": media_id,
                "content_class": content_class,
                "eligibility_status": status,
                "blocked_reason": reason,
                "request_shape_redacted": shape,
                "query_hash_status": "present_valid" if status == "eligible" else "blocked",
                "query_hash": query_hash if status == "eligible" else None,
                "input_kind": INPUT_KIND,
                "would_send_original": False,
                "would_send_thumbnail": False,
                "would_send_derived_image": status == "eligible",
                "local_path_included": False,
                "filename_included": False,
                "source_label_included": False,
            }
        )
        private_rows.append(
            {
                "media_id": int(media.id),
                "found": True,
                "file_type": _file_type_label(media.file_type),
                "content_class": content_class,
                "eligibility_status": status,
                "blocked_reason": reason,
                "original_status": original.status,
                "thumbnail_status": thumbnail.status,
                "original_dimensions": original.dimensions,
                "thumbnail_dimensions": thumbnail.dimensions,
                "source_field_read_for_fallback": False,
            }
        )
    eligible_count = sum(1 for row in request_plan if row["eligibility_status"] == "eligible")
    sample_gate = {
        "approved_sample_media_ids": list(APPROVED_SAMPLE_IDS),
        "requested_media_ids": media_ids,
        "approved_sample_count": len(APPROVED_SAMPLE_IDS),
        "requested_count": len(media_ids),
        "found_media_count": len(by_id),
        "eligible_count": eligible_count,
        "blocked_count": len(request_plan) - eligible_count,
        "blocked_count_by_reason": dict(sorted(blocked_reasons.items())),
        "content_class_distribution": dict(sorted(content_distribution.items())),
        "request_plan": request_plan,
        "private_sample_details": private_rows,
    }
    return sample_gate, by_id


def _safe_filename(media_id: int) -> str:
    return f"phase44d1g_m{int(media_id)}_derived.jpg"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generate_derived_input(media: Media, *, storage_root: Path, output_dir: Path) -> DerivedInput:
    original_path = _resolve_storage_path(storage_root, media.path)
    thumbnail_path = _resolve_storage_path(storage_root, media.thumbnail_path)
    source_path = original_path
    source_kind = "app_managed_original"
    if source_path is None or not source_path.exists() or not source_path.is_file():
        source_path = thumbnail_path
        source_kind = "app_managed_thumbnail"
    if source_path is None:
        raise PrivacyBlocked("derived_input_blocked: source path is unsafe")
    if not source_path.exists() or not source_path.is_file():
        raise PrivacyBlocked("derived_input_blocked: app-managed source is missing")
    safe_filename = _safe_filename(int(media.id))
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = (output_dir / safe_filename).resolve()
    if not _path_relative_to(output_path, output_dir.resolve()):
        raise PrivacyBlocked("derived_input_blocked: safe output path escaped derived directory")
    with Image.open(source_path) as image:
        normalized = ImageOps.exif_transpose(image)
        normalized.thumbnail((MAX_DERIVED_DIMENSION, MAX_DERIVED_DIMENSION), Image.Resampling.LANCZOS)
        if normalized.mode != "RGB":
            normalized = normalized.convert("RGB")
        normalized.save(output_path, DERIVED_FORMAT, quality=90, optimize=True)
    with Image.open(output_path) as derived:
        width = int(derived.width)
        height = int(derived.height)
    return DerivedInput(
        media_id=int(media.id),
        artifact_id=f"approved_media_id:{int(media.id)}:derived:{TRANSFORM_POLICY_VERSION}",
        safe_filename=safe_filename,
        source_kind=source_kind,
        width=width,
        height=height,
        size_bytes=int(output_path.stat().st_size),
        sha256=_sha256_file(output_path),
        path=output_path,
    )


def _host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return urlparse(url).netloc.lower() or None
    except Exception:
        return None


def _is_source_like_host(host: str | None) -> bool:
    if not host:
        return False
    host = host.lower()
    return any(host == pattern or host.endswith("." + pattern) for pattern in SOURCE_LIKE_HOST_PATTERNS)


def _short_text(value: Any, limit: int = 180) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = " ".join(value.split())
    return stripped[:limit] if stripped else None


def run_google_vision_request(*, gcloud_path: str, derived: DerivedInput, max_results: int = MAX_RESULTS) -> dict[str, Any]:
    token_proc = _run_gcloud(gcloud_path, ["auth", "application-default", "print-access-token"], timeout=60)
    if token_proc.returncode != 0 or not (token_proc.stdout or "").strip():
        return {"status": "credential_error", "error_class": "blocked_google_adc_token_unavailable"}
    token = (token_proc.stdout or "").strip()
    try:
        image_content = base64.b64encode(derived.path.read_bytes()).decode("ascii")
        payload = {
            "requests": [
                {
                    "image": {"content": image_content},
                    "features": [{"type": "WEB_DETECTION", "maxResults": int(max_results)}],
                    "imageContext": {},
                }
            ]
        }
        request = urllib.request.Request(
            VISION_ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "x-goog-user-project": GOOGLE_PROJECT_ID,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8")
            status_code = int(response.status)
        raw = json.loads(body)
        return {"status": "completed", "status_code": status_code, "raw_response": raw}
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        error_class = "provider_error"
        if exc.code in {401, 403}:
            error_class = "credential_or_permission_error"
        elif exc.code == 429:
            error_class = "quota_error"
        return {"status": "provider_error", "status_code": exc.code, "error_class": error_class, "error_body_redacted": error_body[:1000]}
    except urllib.error.URLError:
        return {"status": "provider_error", "error_class": "network_error"}
    except TimeoutError:
        return {"status": "provider_error", "error_class": "timeout"}
    finally:
        try:
            del token
        except UnboundLocalError:
            pass


def extract_web_detection(media_id: int, raw_result: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    response = {}
    if raw_result.get("status") == "completed":
        responses = raw_result.get("raw_response", {}).get("responses", [])
        if responses and isinstance(responses[0], dict):
            response = responses[0]
    if "error" in response:
        public = {
            "media_id": media_id,
            "classification": "provider_error",
            "error_class": "provider_error",
            "useful_source_like_match": False,
        }
        return public, {"media_id": media_id, "raw_response": raw_result}
    web = response.get("webDetection") if isinstance(response.get("webDetection"), dict) else {}
    entities = web.get("webEntities") if isinstance(web.get("webEntities"), list) else []
    full_images = web.get("fullMatchingImages") if isinstance(web.get("fullMatchingImages"), list) else []
    partial_images = web.get("partialMatchingImages") if isinstance(web.get("partialMatchingImages"), list) else []
    pages = web.get("pagesWithMatchingImages") if isinstance(web.get("pagesWithMatchingImages"), list) else []
    similar = web.get("visuallySimilarImages") if isinstance(web.get("visuallySimilarImages"), list) else []
    labels = web.get("bestGuessLabels") if isinstance(web.get("bestGuessLabels"), list) else []

    page_hosts = [_host(item.get("url")) for item in pages if isinstance(item, dict)]
    full_hosts = [_host(item.get("url")) for item in full_images if isinstance(item, dict)]
    partial_hosts = [_host(item.get("url")) for item in partial_images if isinstance(item, dict)]
    source_like_hosts = sorted({host for host in [*page_hosts, *full_hosts, *partial_hosts] if _is_source_like_host(host)})
    entity_descriptions = [
        _short_text(item.get("description"), 120)
        for item in entities[:10]
        if isinstance(item, dict) and _short_text(item.get("description"), 120)
    ]
    best_guess_labels = [
        _short_text(item.get("label"), 120)
        for item in labels[:5]
        if isinstance(item, dict) and _short_text(item.get("label"), 120)
    ]

    if raw_result.get("status") != "completed":
        classification = raw_result.get("error_class") or raw_result.get("status") or "provider_error"
    elif full_images and source_like_hosts:
        classification = "exact_source_candidate"
    elif pages and source_like_hosts:
        classification = "likely_source_page"
    elif full_images:
        classification = "likely_source_page"
    elif partial_images or pages:
        classification = "partial_match_only"
    elif similar:
        classification = "visually_similar_only"
    elif entities or labels:
        classification = "broad_web_entity_only"
    else:
        classification = "no_useful_match"

    public = {
        "media_id": media_id,
        "classification": classification,
        "web_entity_count": len(entities),
        "full_matching_image_count": len(full_images),
        "partial_matching_image_count": len(partial_images),
        "pages_with_matching_images_count": len(pages),
        "visually_similar_image_count": len(similar),
        "best_guess_label_count": len(labels),
        "source_like_hosts": source_like_hosts[:10],
        "host_counts": dict(Counter(host for host in [*page_hosts, *full_hosts, *partial_hosts] if host)),
        "web_entity_summary": entity_descriptions[:8],
        "best_guess_label_summary": best_guess_labels,
        "useful_source_like_match": bool(source_like_hosts and (full_images or pages)),
        "artist_work_character_clue_present": bool(entity_descriptions or best_guess_labels),
        "urls_in_public_summary": False,
    }
    details = {
        "media_id": media_id,
        "classification": classification,
        "raw_google_vision_result": raw_result,
        "extracted_urls": {
            "pages": [item.get("url") for item in pages if isinstance(item, dict)],
            "full_matching_images": [item.get("url") for item in full_images if isinstance(item, dict)],
            "partial_matching_images": [item.get("url") for item in partial_images if isinstance(item, dict)],
            "visually_similar_images": [item.get("url") for item in similar if isinstance(item, dict)],
        },
        "extracted_web_entities": [
            {"description": item.get("description"), "score": item.get("score"), "entityId": item.get("entityId")}
            for item in entities
            if isinstance(item, dict)
        ],
        "best_guess_labels": [item for item in labels if isinstance(item, dict)],
        "contains_access_token": False,
    }
    return public, details


def extract_pixiv_filename_prior_from_text(value: str | None) -> list[dict[str, Any]]:
    if not value:
        return []
    matches = []
    for match in PIXIV_PRIOR_RE.finditer(str(value)):
        matches.append(
            {
                "pixiv_work_id": match.group("pixiv_work_id"),
                "page_index": int(match.group("page_index")),
                "token": match.group(0),
            }
        )
    return matches


def _basename_from_metadata(value: str | None) -> str | None:
    if not value:
        return None
    text_value = str(value).replace("\\", "/")
    return text_value.rsplit("/", 1)[-1]


def _metadata_fields_for_pixiv(media: Media) -> list[tuple[str, str | None]]:
    return [
        ("filename", media.filename),
        ("path_basename", _basename_from_metadata(media.path)),
        ("thumbnail_basename", _basename_from_metadata(media.thumbnail_path)),
        ("source_basename", _basename_from_metadata(media.source)),
    ]


def audit_pixiv_source_priors(db: Session, *, approved_ids: Iterable[int]) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = db.query(Media).all()
    approved_set = set(int(item) for item in approved_ids)
    media_with_prior = 0
    work_ids: Counter[str] = Counter()
    page_indexes: Counter[str] = Counter()
    by_content_class: Counter[str] = Counter()
    approved_with_prior = 0
    details: list[dict[str, Any]] = []
    fields_with_matches: Counter[str] = Counter()
    fields_present: Counter[str] = Counter()
    for media in rows:
        media_matches: list[dict[str, Any]] = []
        content_class = _content_class_label(media.content_class)
        for field_name, value in _metadata_fields_for_pixiv(media):
            if value:
                fields_present[field_name] += 1
            for match in extract_pixiv_filename_prior_from_text(value):
                fields_with_matches[field_name] += 1
                media_matches.append(
                    {
                        "field": field_name,
                        "pixiv_work_id": match["pixiv_work_id"],
                        "page_index": match["page_index"],
                        "token": match["token"],
                        "sanitized_basename": _basename_from_metadata(value),
                    }
                )
        if media_matches:
            media_with_prior += 1
            by_content_class[content_class] += 1
            if int(media.id) in approved_set:
                approved_with_prior += 1
            seen_work_ids = {item["pixiv_work_id"] for item in media_matches}
            seen_token_pages = {
                (item["pixiv_work_id"], int(item["page_index"]))
                for item in media_matches
            }
            for work_id in seen_work_ids:
                work_ids[work_id] += 1
            for _work_id, page_index in seen_token_pages:
                page_indexes[str(page_index)] += 1
            details.append(
                {
                    "media_id": int(media.id),
                    "content_class": content_class,
                    "matches": media_matches,
                    "classification": "pixiv_filename_source_prior",
                }
            )
    duplicate_work_id_count = sum(1 for count in work_ids.values() if count > 1)
    total = len(rows)
    summary = {
        "audit_scope": "development_db_app_managed_metadata_only",
        "source_roots_scanned": False,
        "icloud_touched": False,
        "cloud_files_hydrated": False,
        "original_source_files_read": False,
        "total_media_inspected": total,
        "media_with_pixiv_like_filename_token": media_with_prior,
        "coverage_percent": round((media_with_prior / total * 100), 2) if total else 0.0,
        "distinct_candidate_pixiv_work_ids": len(work_ids),
        "duplicate_work_id_count": duplicate_work_id_count,
        "page_index_distribution": dict(sorted(page_indexes.items(), key=lambda item: int(item[0]))),
        "count_by_content_class": dict(sorted(by_content_class.items())),
        "approved_sample_pixiv_prior_count": approved_with_prior,
        "approved_samples_are_representative_for_pixiv_prior": approved_with_prior > 0,
        "exact_pixiv_ids_in_public_report": False,
        "metadata_fields_present_counts": dict(sorted(fields_present.items())),
        "metadata_fields_with_matches": dict(sorted(fields_with_matches.items())),
        "metadata_retention_assessment": (
            "filename_and_app_managed_basenames_available_but_no_dedicated_original_basename_column"
        ),
        "metadata_retention_gap": True,
    }
    private = {
        "summary": summary,
        "details": details,
        "contains_local_absolute_paths": False,
        "contains_exact_pixiv_ids": True,
    }
    return summary, private


def install_read_only_guard(engine: Any) -> None:
    @event.listens_for(engine, "before_cursor_execute")
    def _guard(_conn: Any, _cursor: Any, statement: str, _parameters: Any, _context: Any, _executemany: bool) -> None:
        if WRITE_SQL_RE.search(statement or ""):
            raise ReadOnlyViolation("read_only_guard_blocked_write_sql")


def prove_db_identity(session: Session, config: ProjectConfig) -> dict[str, Any]:
    actual_db = session.execute(text("SELECT current_database()")).scalar()
    if str(actual_db) != "blombooru" or config.db_name != "blombooru":
        raise IdentityBlockedError(f"identity_blocked: expected DB blombooru, got {config.db_name!r}/{actual_db!r}")
    return {
        "violet_env": config.violet_env,
        "configured_db_host": config.db_host,
        "configured_db_port": config.db_port,
        "configured_db_user": config.db_user,
        "configured_db_name": config.db_name,
        "actual_db_name": str(actual_db),
        "db_identity_result": "development_blombooru_confirmed",
        "db_password_included": False,
        "storage_root_mode": "explicit_storage_root" if config.storage_root_explicitly_set else "code_root_default",
        "storage_root_explicitly_set": config.storage_root_explicitly_set,
        "storage_root_test_path": _is_test_storage_path(config.storage_root),
        "storage_root_equals_code_root": config.storage_root.resolve() == config.project_root.resolve(),
        "local_paths_redacted": True,
    }


def _public_derived_row(derived: DerivedInput) -> dict[str, Any]:
    return {
        "media_id": derived.media_id,
        "artifact_id": derived.artifact_id,
        "safe_filename": derived.safe_filename,
        "source_kind": derived.source_kind,
        "width": derived.width,
        "height": derived.height,
        "size_bytes": derived.size_bytes,
        "sha256_public": "<redacted>",
    }


def _private_derived_row(derived: DerivedInput) -> dict[str, Any]:
    return {
        "media_id": derived.media_id,
        "artifact_id": derived.artifact_id,
        "safe_filename": derived.safe_filename,
        "source_kind": derived.source_kind,
        "width": derived.width,
        "height": derived.height,
        "size_bytes": derived.size_bytes,
        "sha256": derived.sha256,
        "path_included": False,
    }


def _aggregate_google_results(items: list[dict[str, Any]]) -> dict[str, Any]:
    host_counter: Counter[str] = Counter()
    class_counter: Counter[str] = Counter()
    useful = 0
    clue = 0
    for item in items:
        class_counter[str(item.get("classification"))] += 1
        host_counter.update(item.get("host_counts", {}))
        useful += 1 if item.get("useful_source_like_match") else 0
        clue += 1 if item.get("artist_work_character_clue_present") else 0
    return {
        "classification_counts": dict(sorted(class_counter.items())),
        "source_like_match_count": useful,
        "artist_work_character_clue_item_count": clue,
        "top_hosts": dict(host_counter.most_common(15)),
        "exact_or_likely_source_candidate_count": sum(
            1 for item in items if item.get("classification") in {"exact_source_candidate", "likely_source_page"}
        ),
    }


def sauce_comparison(google_items: list[dict[str, Any]], pixiv_approved_count: int) -> dict[str, Any]:
    saucenao = {
        2687: "high_confidence_correct_artist_work_character_useful",
        2670: "high_confidence_correct_artist_work_character_useful",
        2690: "low_confidence_wrong_unrelated",
        2654: "low_confidence_wrong_unrelated",
        2647: "low_confidence_wrong_unrelated",
    }
    by_id = {int(item["media_id"]): item for item in google_items}
    return {
        "known_saucenao_manual_validation": saucenao,
        "google_on_saucenao_successes": {
            str(media_id): by_id.get(media_id, {}).get("classification", "not_run") for media_id in (2687, 2670)
        },
        "google_on_saucenao_failures": {
            str(media_id): by_id.get(media_id, {}).get("classification", "not_run") for media_id in (2690, 2654, 2647)
        },
        "google_rescued_saucenao_failure_count": sum(
            1
            for media_id in (2690, 2654, 2647)
            if by_id.get(media_id, {}).get("classification") in {"exact_source_candidate", "likely_source_page"}
        ),
        "google_structured_metadata_comparable_to_saucenao": False,
        "pixiv_prior_covered_approved_sample_count": pixiv_approved_count,
        "approved_samples_representative_for_pixiv_prior": pixiv_approved_count > 0,
    }


def contract_fit() -> dict[str, Any]:
    return {
        "google_vision": {
            "ProviderQuery": {
                "provider_key": PROVIDER_KEY,
                "input_kind": INPUT_KIND,
                "query_hash": "derived input hash kept local; public summary records presence only",
                "request_shape_redacted": "WEB_DETECTION images:annotate request without local path, filename, token, or bytes",
            },
            "ProviderRunOutcome": "completed/partial/provider_error/credential_error/quota_error can be represented",
            "SourceMatch": "matching page and image URLs can map to source_host, rank, score when present, and match_class",
            "ExtractedProviderMetadata": "web entity descriptions and best guess labels are untyped clues with localization_status=pending",
            "EvidencePersistencePlan": {
                "db_write_allowed": False,
                "provider_cache_planned": False,
                "entity_evidence_planned": False,
                "media_entity_candidate_planned": False,
                "confirmed_assignment_allowed": False,
            },
        },
        "pixiv_filename_prior": {
            "classification": "local_source_prior_not_provider_result",
            "future_contract_need": "SourcePrior or LocalSourceHint extension",
            "not_forced_into_provider_cache": True,
            "not_confirmed_evidence": True,
        },
    }


def build_public_summary(
    *,
    generated_at: str,
    google_preflight_result: dict[str, Any],
    identity: dict[str, Any],
    sample_gate: dict[str, Any],
    derived_inputs: list[DerivedInput],
    google_items: list[dict[str, Any]],
    pixiv_summary: dict[str, Any],
) -> dict[str, Any]:
    google_aggregate = _aggregate_google_results(google_items)
    summary = {
        "phase": PHASE,
        "title": "Google Vision Web Detection Tiny Pilot + Pixiv Filename Source-Prior Audit",
        "generated_at": generated_at,
        "status": "completed",
        "lifecycle": "phase-scoped operational runner",
        "google_setup_confirmation": {
            key: google_preflight_result[key]
            for key in (
                "gcloud_discovery_status",
                "gcloud_path_category",
                "gcloud_absolute_path_used",
                "gcloud_version_first_line",
                "active_account_present",
                "active_account_count",
                "project",
                "project_ok",
                "quota_project",
                "quota_project_ok",
                "vision_api_enabled",
                "adc_token_available",
                "adc_token_printed",
                "google_application_credentials_set",
                "google_application_credentials_outside_repo",
                "google_application_credentials_path_printed",
                "credential_contents_printed",
            )
        },
        "identity": identity,
        "approved_sample_ids": list(APPROVED_SAMPLE_IDS),
        "sample_gate": {key: value for key, value in sample_gate.items() if key != "private_sample_details"},
        "derived_upload_count": len(derived_inputs),
        "original_upload_count": 0,
        "google_vision_request_count": len(google_items),
        "google_vision_results": google_items,
        "google_vision_aggregate": google_aggregate,
        "pixiv_filename_source_prior_audit": pixiv_summary,
        "saucenao_comparison": sauce_comparison(google_items, pixiv_summary["approved_sample_pixiv_prior_count"]),
        "contract_fit": contract_fit(),
        "next_step_recommendation": next_step_recommendation(google_aggregate, pixiv_summary),
        "local_artifacts": {
            "google_vision_details": ".local_manifests/phase-4.4d1g-google-vision-details.json",
            "google_vision_derived": ".local_manifests/phase-4.4d1g-google-vision-derived/",
            "manual_validation_sheet": ".local_manifests/phase-4.4d1g-google-vision-manual-validation-sheet.md",
            "pixiv_source_prior_details": ".local_manifests/phase-4.4d1g-pixiv-source-prior-details.json",
            "ignored_by_gitignore": True,
        },
        "safety_confirmation": {
            "db_write": False,
            "db_migration": False,
            "provider_cache_write": False,
            "entity_evidence_write": False,
            "media_entity_candidate_write": False,
            "confirmed_assignment": False,
            "automatic_entity_creation": False,
            "media_tags_mutation": False,
            "tag_translation_mutation": False,
            "localization_execution": False,
            "entity_resolver": False,
            "similarity_clustering": False,
            "source_icloud_mutation": False,
            "app_managed_storage_mutation": False,
            "original_image_upload": False,
            "unapproved_sample_upload": False,
            "saucenao_call": False,
            "tineye_call": False,
            "pixiv_call": False,
            "danbooru_gelbooru_call": False,
            "scraping": False,
            "browser_automation": False,
            "cookies": False,
            "credential_or_token_content_printed": False,
        },
        "public_redaction": {
            "local_absolute_paths": False,
            "original_filenames": False,
            "source_icloud_paths": False,
            "api_token": False,
            "credential_path": False,
            "raw_image_bytes": False,
            "raw_provider_payload": False,
            "exact_pixiv_id_mapping": False,
            "provider_urls": False,
        },
    }
    assert_public_report_safe(summary)
    return summary


def assert_public_report_safe(payload: Any) -> None:
    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str):
            if LOCAL_PATH_RE.search(value):
                raise PrivacyBlocked("public_report_contains_local_path")
            if SECRET_TEXT_RE.search(value):
                raise PrivacyBlocked("public_report_contains_secret_like_text")

    visit(payload)
    json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)


def next_step_recommendation(google_aggregate: dict[str, Any], pixiv_summary: dict[str, Any]) -> dict[str, Any]:
    if pixiv_summary["media_with_pixiv_like_filename_token"] > 0:
        recommended = "Phase 4.4-P0 - Pixiv Filename Source-Prior Metadata Lookup Design"
    elif google_aggregate["exact_or_likely_source_candidate_count"] > 0:
        recommended = "Google Vision manual validation / follow-up"
    else:
        recommended = "broader SauceNAO/Google sample validation or known-source metadata lookup design"
    return {
        "recommended": recommended,
        "google_followup_useful": google_aggregate["exact_or_likely_source_candidate_count"] > 0,
        "pixiv_prior_route_non_trivial": pixiv_summary["media_with_pixiv_like_filename_token"] > 0,
        "danbooru_gelbooru_lookup_now": "only after known source/post IDs exist; no lookup was run in D1G",
        "tineye_status": "rejected_or_deferred_due_to_cost_and_weaker_task_fit_for_this_phase",
    }


def build_markdown_report(summary: dict[str, Any]) -> str:
    google = summary["google_vision_aggregate"]
    pixiv = summary["pixiv_filename_source_prior_audit"]
    sample_note = (
        "The approved five samples are not representative for Pixiv-prior coverage."
        if pixiv["approved_sample_pixiv_prior_count"] == 0
        else "At least one approved sample has Pixiv-prior coverage."
    )
    lines = [
        "# Phase 4.4-D1G - Google Vision Tiny Pilot and Pixiv Source-Prior Audit",
        "",
        f"Date: {summary['generated_at']}",
        "",
        "## Summary",
        "",
        "- Google Vision Web Detection ran on the five approved anime samples using derived/resized/metadata-stripped images only.",
        "- Pixiv filename source-prior audit scanned DB/app-managed metadata strings only and did not touch source roots or iCloud.",
        "- This stage does not persist provider evidence and does not write DB rows.",
        "",
        "## Google Setup Confirmation",
        "",
        f"- gcloud discovery: `{summary['google_setup_confirmation']['gcloud_discovery_status']}` via `{summary['google_setup_confirmation']['gcloud_path_category']}`.",
        f"- Project: `{summary['google_setup_confirmation']['project']}`; quota project: `{summary['google_setup_confirmation']['quota_project']}`.",
        f"- Vision API enabled: `{summary['google_setup_confirmation']['vision_api_enabled']}`.",
        f"- ADC token available: `{summary['google_setup_confirmation']['adc_token_available']}` (redacted; token not printed).",
        f"- GOOGLE_APPLICATION_CREDENTIALS set: `{summary['google_setup_confirmation']['google_application_credentials_set']}`; credential path printed: `False`.",
        "",
        "## Approved Sample Gate",
        "",
        f"- Approved sample IDs: `{', '.join(str(item) for item in summary['approved_sample_ids'])}`.",
        f"- Found media: `{summary['sample_gate']['found_media_count']}` / `{summary['sample_gate']['requested_count']}`.",
        f"- Eligible media: `{summary['sample_gate']['eligible_count']}`; blocked: `{summary['sample_gate']['blocked_count']}`.",
        f"- Content-class distribution: `{json.dumps(summary['sample_gate']['content_class_distribution'], sort_keys=True)}`.",
        "",
        "## Google Vision Results",
        "",
        f"- Derived upload count: `{summary['derived_upload_count']}`.",
        f"- Google Vision request count: `{summary['google_vision_request_count']}`.",
        f"- Per-item classification counts: `{json.dumps(google['classification_counts'], sort_keys=True)}`.",
        f"- Exact or likely source candidate count: `{google['exact_or_likely_source_candidate_count']}`.",
        f"- Source-like match count: `{google['source_like_match_count']}`.",
        f"- Artist/work/character clue item count: `{google['artist_work_character_clue_item_count']}`.",
        f"- Top returned hosts: `{json.dumps(google['top_hosts'], sort_keys=True)}`.",
        "",
        "| media_id | classification | source-like hosts | web entities | best guess labels |",
        "| ---: | --- | --- | --- | --- |",
    ]
    for item in summary["google_vision_results"]:
        lines.append(
            "| {media_id} | `{classification}` | `{hosts}` | `{entities}` | `{labels}` |".format(
                media_id=item["media_id"],
                classification=item["classification"],
                hosts=", ".join(item.get("source_like_hosts") or []) or "none",
                entities=", ".join(item.get("web_entity_summary") or [])[:240] or "none",
                labels=", ".join(item.get("best_guess_label_summary") or [])[:160] or "none",
            )
        )
    lines.extend(
        [
            "",
            "## SauceNAO Comparison",
            "",
            "- SauceNAO was previously high-confidence correct for `2687` and `2670`, with useful artist/work/character metadata.",
            "- SauceNAO was previously low-confidence wrong or unrelated for `2690`, `2654`, and `2647`.",
            f"- Google Vision rescue count for those SauceNAO low-confidence failures: `{summary['saucenao_comparison']['google_rescued_saucenao_failure_count']}`.",
            "- Google Vision does not provide structured booru-style artist/work/character metadata comparable to SauceNAO; any such values are indirect web entity or page clues.",
            "",
            "## Pixiv Filename Source-Prior Audit",
            "",
            f"- Total media inspected: `{pixiv['total_media_inspected']}`.",
            f"- Media with Pixiv-like filename token: `{pixiv['media_with_pixiv_like_filename_token']}` (`{pixiv['coverage_percent']}`%).",
            f"- Distinct candidate Pixiv work IDs: `{pixiv['distinct_candidate_pixiv_work_ids']}`.",
            f"- Duplicate work ID count: `{pixiv['duplicate_work_id_count']}`.",
            f"- Page index distribution: `{json.dumps(pixiv['page_index_distribution'], sort_keys=True)}`.",
            f"- Count by content_class: `{json.dumps(pixiv['count_by_content_class'], sort_keys=True)}`.",
            f"- Approved five-sample Pixiv-prior count: `{pixiv['approved_sample_pixiv_prior_count']}`.",
            f"- Representation note: {sample_note}",
            f"- Metadata retention assessment: `{pixiv['metadata_retention_assessment']}`.",
            "- Exact Pixiv IDs, page indexes, and media mappings are kept only in ignored local details.",
            "",
            "## Contract Fit",
            "",
            "- Google Vision can map conceptually to `ProviderQuery`, `ProviderRunOutcome`, `SourceMatch`, and `ExtractedProviderMetadata`, but D1G keeps `db_write_allowed=false` and does not persist.",
            "- Pixiv filename prior is a local deterministic source hint, not a provider result and not confirmed evidence. It should not be forced into `ProviderCache` without a future `SourcePrior` / `LocalSourceHint` design.",
            "",
            "## Next Step",
            "",
            f"- Recommended: `{summary['next_step_recommendation']['recommended']}`.",
            "- TinEye remains rejected/deferred for this phase due to cost and weaker fit than SauceNAO / Google Vision / Pixiv source-prior routes.",
            "",
            "## Safety Confirmation",
            "",
        ]
    )
    for key, value in summary["safety_confirmation"].items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(
        [
            "",
            "## Public Redaction Confirmation",
            "",
        ]
    )
    for key, value in summary["public_redaction"].items():
        lines.append(f"- {key}: `{value}`")
    return "\n".join(lines) + "\n"


def build_manual_validation_sheet(summary: dict[str, Any]) -> str:
    pixiv_details_by_id = {
        int(item["media_id"]): True
        for item in summary.get("_private_pixiv_details", {}).get("details", [])
    }
    lines = [
        "# Phase 4.4-D1G Google Vision Manual Validation Sheet",
        "",
        "| media_id | google_classification | top host summary | web entity summary | best guess label summary | pixiv_filename_prior_exists | exact_same_image | same_image_repost | source_page_correct | same_character_or_work_but_not_same_image | visually_similar_wrong_source | wrong_match | metadata_useful_yes_no | recommended_action |",
        "| ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in summary["google_vision_results"]:
        media_id = int(item["media_id"])
        hosts = ", ".join(item.get("source_like_hosts") or []) or "none"
        entities = ", ".join(item.get("web_entity_summary") or [])[:180] or "none"
        labels = ", ".join(item.get("best_guess_label_summary") or [])[:120] or "none"
        lines.append(
            f"| {media_id} | {item['classification']} | {hosts} | {entities} | {labels} | {str(pixiv_details_by_id.get(media_id, False)).lower()} |  |  |  |  |  |  |  |  |"
        )
    return "\n".join(lines) + "\n"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media-ids", nargs="+", type=int, required=True)
    parser.add_argument("--execute-live", action="store_true", required=True)
    parser.add_argument("--upload-derived-approved", action="store_true", required=True)
    parser.add_argument("--gcloud-path", default=None)
    parser.add_argument("--report-md", default="docs/reports/phase-4.4d1g-google-vision-pixiv-source-prior.md")
    parser.add_argument("--report-json", default="docs/reports/phase-4.4d1g-google-vision-pixiv-source-prior-summary.json")
    parser.add_argument("--google-details-json", default=".local_manifests/phase-4.4d1g-google-vision-details.json")
    parser.add_argument("--pixiv-details-json", default=".local_manifests/phase-4.4d1g-pixiv-source-prior-details.json")
    parser.add_argument("--manual-validation-sheet", default=".local_manifests/phase-4.4d1g-google-vision-manual-validation-sheet.md")
    parser.add_argument("--derived-dir", default=".local_manifests/phase-4.4d1g-google-vision-derived")
    parser.add_argument("--request-delay-seconds", type=float, default=1.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    media_ids = parse_media_ids(args.media_ids)
    if not args.execute_live or not args.upload_derived_approved:
        raise Phase44D1GError("live_google_vision_requires_explicit_execute_and_upload_approval_flags")

    report_md = resolve_output_path(args.report_md, expected_parent=PUBLIC_REPORT_DIR)
    report_json = resolve_output_path(args.report_json, expected_parent=PUBLIC_REPORT_DIR)
    google_details_json = resolve_output_path(args.google_details_json, expected_parent=LOCAL_DETAILS_DIR)
    pixiv_details_json = resolve_output_path(args.pixiv_details_json, expected_parent=LOCAL_DETAILS_DIR)
    manual_sheet = resolve_output_path(args.manual_validation_sheet, expected_parent=LOCAL_DETAILS_DIR)
    derived_dir = resolve_output_path(args.derived_dir, expected_parent=LOCAL_DETAILS_DIR)

    gcloud_discovery = discover_gcloud(args.gcloud_path)
    google_preflight_result = google_preflight(gcloud_discovery)

    config = load_project_config()
    engine = create_engine(config.database_url)
    install_read_only_guard(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        identity = prove_db_identity(session, config)
        sample_gate, media_by_id = build_sample_gate(session, media_ids=media_ids, storage_root=config.storage_root)
        if sample_gate["blocked_count"]:
            raise SampleGateError("sample_gate_blocked: one or more approved samples are ineligible")

        derived_inputs = [
            generate_derived_input(media_by_id[media_id], storage_root=config.storage_root, output_dir=derived_dir)
            for media_id in media_ids
        ]
        google_public_items: list[dict[str, Any]] = []
        google_private_items: list[dict[str, Any]] = []
        for index, derived in enumerate(derived_inputs):
            result = run_google_vision_request(
                gcloud_path=str(gcloud_discovery["selected_path"]),
                derived=derived,
                max_results=MAX_RESULTS,
            )
            public, private = extract_web_detection(derived.media_id, result)
            google_public_items.append(public)
            google_private_items.append(private)
            if public["classification"] in {"credential_or_permission_error", "quota_error", "credential_error"}:
                break
            if index < len(derived_inputs) - 1 and args.request_delay_seconds > 0:
                time.sleep(args.request_delay_seconds)

        pixiv_summary, pixiv_private = audit_pixiv_source_priors(session, approved_ids=media_ids)
    finally:
        session.close()
        engine.dispose()

    generated_at = _now_iso()
    public_summary = build_public_summary(
        generated_at=generated_at,
        google_preflight_result=google_preflight_result,
        identity=identity,
        sample_gate=sample_gate,
        derived_inputs=derived_inputs,
        google_items=google_public_items,
        pixiv_summary=pixiv_summary,
    )
    google_details = {
        "phase": PHASE,
        "generated_at": generated_at,
        "google_preflight_private": {
            **google_preflight_result,
            "adc_token_stdout_length_recorded_private": google_preflight_result.get("adc_token_stdout_length_recorded_private"),
        },
        "sample_gate_private": sample_gate["private_sample_details"],
        "derived_inputs": [_private_derived_row(item) for item in derived_inputs],
        "google_results_private": google_private_items,
        "contains_access_token": False,
        "contains_credential_file_contents": False,
        "contains_local_absolute_paths": False,
    }
    private_summary_for_sheet = dict(public_summary)
    private_summary_for_sheet["_private_pixiv_details"] = pixiv_private

    write_json(google_details_json, google_details)
    write_json(pixiv_details_json, pixiv_private)
    write_text(manual_sheet, build_manual_validation_sheet(private_summary_for_sheet))
    write_json(report_json, public_summary)
    write_text(report_md, build_markdown_report(public_summary))
    print(json.dumps({"status": "completed", "report_json": str(report_json.relative_to(ROOT)), "report_md": str(report_md.relative_to(ROOT))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
