"""Phase 4.4-B1 one-provider live reverse-search pilot runner.

Lifecycle: phase-scoped operational runner. This runner is intentionally narrow:
it accepts only the five approved media IDs, uses one SauceNAO-style provider,
uploads only generated resized/stripped derivatives when all live gates pass,
and writes only cache/evidence records when explicitly requested.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Iterable

import httpx
from PIL import Image, ImageOps
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.enums import (  # noqa: E402
    ContentClassEnum,
    EntityCandidateGeneratorEnum,
    EntityEvidenceTypeEnum,
    FileTypeEnum,
)
from app.models import (  # noqa: E402
    EntityEvidence,
    Media,
    MediaEntityAssignment,
    MediaEntityCandidate,
    NegativeLookupCache,
    ProviderCache,
)
from app.services.entity_metadata_service import hash_provider_query, record_evidence  # noqa: E402


logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

APPROVED_SAMPLE_IDS = (2690, 2687, 2670, 2654, 2647)
APPROVED_SAMPLE_SET = frozenset(APPROVED_SAMPLE_IDS)
PHASE = "4.4-B1"
PROVIDER_KEY = "saucenao"
PROVIDER_CATEGORY = "saucenao_style_reverse_search"
QUERY_TYPE = "reverse_search_derived_image"
INPUT_KIND = "derived_resized_stripped_image"
TRANSFORM_POLICY_VERSION = "phase44b1-derived-resized-stripped-v1"
MAX_DERIVED_DIMENSION = 768
DERIVED_FORMAT = "JPEG"
DERIVED_MIME_TYPE = "image/jpeg"
HIGH_CONFIDENCE_THRESHOLD = 85.0
LOW_CONFIDENCE_THRESHOLD = 60.0
CONFLICT_DELTA_THRESHOLD = 2.0
REQUESTS_PER_MINUTE = 6
SHORT_QUOTA_WAIT_SECONDS = 45
MAX_TOTAL_QUOTA_WAIT_SECONDS = 90
MAX_FAILURES = 2
MAX_CONSECUTIVE_FAILURES = 2
MAX_SAME_REASON_FAILURES = 2
PUBLIC_REPORT_DIR = Path("docs/reports")
LOCAL_DETAILS_DIR = Path(".local_manifests")
ACCEPTABLE_SERVER_PREFLIGHT_BACKENDS = frozenset({"windows_netstat"})
ALLOWED_WRITE_TABLES = frozenset(
    {
        "ProviderCache",
        "NegativeLookupCache",
        "EntityEvidence",
        "MediaEntityCandidate",
    }
)
FORBIDDEN_WRITE_TABLES = (
    "MediaEntityAssignment",
    "Entity",
    "media_tags",
    "TagTranslation",
)


class Phase44B1Error(RuntimeError):
    pass


class SampleGateError(Phase44B1Error):
    pass


class EnvBlockedError(Phase44B1Error):
    pass


class ServerPreflightBlockedError(Phase44B1Error):
    pass


class IdentityBlockedError(Phase44B1Error):
    pass


class OutputPathError(Phase44B1Error):
    pass


class CredentialRequired(Phase44B1Error):
    pass


class ProviderPolicyBlocked(Phase44B1Error):
    pass


class ProviderStop(ProviderPolicyBlocked):
    def __init__(
        self,
        stop_condition: str,
        *,
        public_results: list[dict[str, Any]],
        details_results: list[dict[str, Any]],
        result_counts: dict[str, int],
        db_counts: dict[str, int],
        total_wait_seconds: int = 0,
    ) -> None:
        super().__init__(f"provider_stop: {stop_condition}")
        self.stop_condition = stop_condition
        self.public_results = public_results
        self.details_results = details_results
        self.result_counts = result_counts
        self.db_counts = db_counts
        self.total_wait_seconds = total_wait_seconds


class PrivacyBlocked(Phase44B1Error):
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


@dataclass(frozen=True)
class ProviderResult:
    media_id: int
    result_class: str
    response_status: str
    error_class: str | None
    score: float | None
    normalized_payload: dict[str, Any]
    retry_after_seconds: int | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().replace(microsecond=0).isoformat()


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
    violet_env = violet_env_raw.lower()
    if violet_env != "development":
        reported_env = violet_env_raw or "unset"
        raise EnvBlockedError(
            "env_blocked: VIOLET_ENV must be 'development' for Phase 4.4-B1; "
            f"got {reported_env!r}"
        )
    if _env_value(dotenv_values, "TEST_DATABASE_URL", "").strip():
        raise EnvBlockedError("env_blocked: TEST_DATABASE_URL is set; refusing development live pilot")

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


def get_saucenao_api_key(project_root: Path = ROOT) -> str | None:
    dotenv_values = _read_dotenv(project_root / ".env")
    value = _env_value(dotenv_values, "SAUCENAO_API_KEY", "").strip()
    return value or None


def _path_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _public_path_label(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return path.name


def resolve_output_path(raw_path: str, *, expected_parent: Path) -> Path:
    raw_text = str(raw_path)
    if raw_text.startswith("\\\\") or raw_text.startswith("//") or re.match(r"(?i)^z:[\\/]", raw_text):
        raise OutputPathError("output_path_blocked: NAS/network-share paths are not allowed")
    candidate = Path(raw_path)
    resolved = candidate.resolve() if candidate.is_absolute() else (ROOT / candidate).resolve()
    if not _path_relative_to(resolved, ROOT.resolve()):
        raise OutputPathError("output_path_blocked: output must stay under repository root")
    if not _path_relative_to(resolved, (ROOT / expected_parent).resolve()):
        raise OutputPathError(f"output_path_blocked: output must stay under {_public_path_label(expected_parent)}")
    return resolved


def parse_media_ids(media_ids: Iterable[int] | None) -> list[int]:
    values = list(media_ids or [])
    if not values:
        raise SampleGateError("sample_gate_blocked: --media-ids is required and must be explicit")
    requested = list(dict.fromkeys(int(item) for item in values))
    outside = [item for item in requested if item not in APPROVED_SAMPLE_SET]
    if outside:
        raise SampleGateError(
            "sample_gate_blocked: media IDs outside the approved Phase 4.4-B1 sample set: "
            + ", ".join(str(item) for item in outside)
        )
    omitted = [item for item in APPROVED_SAMPLE_IDS if item not in requested]
    if omitted:
        raise SampleGateError(
            "sample_gate_blocked: exact approved sample set is required; omitted IDs: "
            + ", ".join(str(item) for item in omitted)
        )
    return requested


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
    if original.status != "present":
        return f"blocked_by_original:{original.status}"
    if thumbnail.status != "present":
        return f"blocked_by_thumbnail:{thumbnail.status}"
    return None


def _safe_filename(media_id: int) -> str:
    return f"phase44b1_m{int(media_id)}_derived.jpg"


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generate_derived_input(media: Media, *, storage_root: Path, output_dir: Path) -> DerivedInput:
    source_path = _resolve_storage_path(storage_root, media.path)
    source_kind = "app_managed_original"
    if source_path is None or not source_path.exists() or not source_path.is_file():
        source_path = _resolve_storage_path(storage_root, media.thumbnail_path)
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
        if normalized.mode not in {"RGB", "L"}:
            normalized = normalized.convert("RGB")
        elif normalized.mode == "L":
            normalized = normalized.convert("RGB")
        normalized.save(output_path, DERIVED_FORMAT, quality=90, optimize=True)

    width = height = 0
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


def make_request_shape(*, media_id: int, content_class: str, derived_sha256: str | None = None) -> tuple[dict[str, Any], str]:
    public_shape = {
        "phase": PHASE,
        "provider_key": PROVIDER_KEY,
        "provider_category": PROVIDER_CATEGORY,
        "query_type": QUERY_TYPE,
        "input_kind": INPUT_KIND,
        "input_privacy_mode": "derived_upload_approved_sample_only",
        "media_ref": f"approved_media_id:{int(media_id)}",
        "content_class": content_class,
        "transform_policy_version": TRANSFORM_POLICY_VERSION,
        "max_derived_dimension": MAX_DERIVED_DIMENSION,
        "send_original": False,
        "send_thumbnail": False,
        "send_derived": True,
        "local_path_included": False,
        "filename_included": False,
        "source_label_included": False,
    }
    private_hash_shape = dict(public_shape)
    if derived_sha256:
        private_hash_shape["derived_sha256_private"] = derived_sha256
    query_hash = hash_provider_query(private_hash_shape)
    return public_shape, query_hash


def build_saucenao_request(api_key: str, derived: DerivedInput) -> dict[str, Any]:
    if not api_key:
        raise CredentialRequired("credential_required: SAUCENAO_API_KEY is missing")
    with derived.path.open("rb") as handle:
        content = handle.read()
    return {
        "method": "POST",
        "url": "https://saucenao.com/search.php",
        "params": {
            "output_type": "2",
            "api_key": api_key,
            "db": "999",
            "numres": "16",
            "dedupe": "2",
        },
        "files": {
            "file": (derived.safe_filename, content, DERIVED_MIME_TYPE),
        },
        "redacted_request_shape": {
            "method": "POST",
            "url": "https://saucenao.com/search.php",
            "params": {
                "output_type": "2",
                "api_key": "<redacted>",
                "db": "999",
                "numres": "16",
                "dedupe": "2",
            },
            "multipart_file_field": "file",
            "multipart_filename": derived.safe_filename,
            "multipart_content_type": DERIVED_MIME_TYPE,
            "local_path_included": False,
            "original_filename_included": False,
            "source_label_included": False,
            "original_upload": False,
            "derived_upload": True,
        },
    }


def _parse_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_int(value: Any) -> int | None:
    parsed = _parse_float(value)
    if parsed is None:
        return None
    return int(parsed)


def _saucenao_header_summary(header: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "status": _parse_int(header.get("status")),
        "short_remaining": _parse_int(header.get("short_remaining")),
        "long_remaining": _parse_int(header.get("long_remaining")),
        "minimum_similarity": _parse_float(header.get("minimum_similarity")),
    }
    for key in ("message", "long_message", "status_message", "error"):
        value = header.get(key)
        if isinstance(value, str) and value.strip():
            summary[key] = value.strip()[:300]
    return summary


def _first_public_url(data: dict[str, Any]) -> str | None:
    ext_urls = data.get("ext_urls")
    if isinstance(ext_urls, list):
        for item in ext_urls:
            if isinstance(item, str) and item.startswith(("https://", "http://")):
                return item
    source = data.get("source")
    if isinstance(source, str) and source.startswith(("https://", "http://")):
        return source
    return None


def _safe_title(data: dict[str, Any]) -> str | None:
    for key in ("title", "eng_name", "jp_name", "material"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:300]
    return None


def _safe_creator(data: dict[str, Any]) -> str | None:
    for key in ("member_name", "creator", "author_name", "author", "pawoo_user_display_name"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:300]
    return None


def _normalize_result_entry(entry: dict[str, Any]) -> dict[str, Any]:
    header = entry.get("header") if isinstance(entry.get("header"), dict) else {}
    data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
    score = _parse_float(header.get("similarity"))
    return {
        "similarity": score,
        "index_id": header.get("index_id"),
        "index_name": header.get("index_name"),
        "result_id": header.get("result_id") or data.get("pixiv_id") or data.get("danbooru_id") or data.get("gelbooru_id"),
        "title": _safe_title(data),
        "creator": _safe_creator(data),
        "source_url_present": bool(_first_public_url(data)),
        "source_url_host": _url_host(_first_public_url(data)),
    }


def _url_host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        from urllib.parse import urlparse

        return urlparse(url).netloc.lower() or None
    except Exception:
        return None


def classify_saucenao_response(*, media_id: int, status_code: int, headers: dict[str, Any], payload: Any) -> ProviderResult:
    retry_after = headers.get("retry-after") or headers.get("Retry-After") if headers else None
    retry_after_seconds = None
    if retry_after is not None:
        try:
            retry_after_seconds = int(str(retry_after))
        except ValueError:
            retry_after_seconds = None

    if status_code == 401:
        return ProviderResult(media_id, "auth_failed", "error", "auth_failed", None, {}, retry_after_seconds)
    if status_code == 403:
        return ProviderResult(media_id, "forbidden", "error", "forbidden", None, {}, retry_after_seconds)
    if status_code == 429:
        return ProviderResult(media_id, "rate_limited", "error", "rate_limited", None, {}, retry_after_seconds)
    if status_code >= 500:
        return ProviderResult(media_id, "provider_error", "error", "provider_5xx", None, {}, retry_after_seconds)
    if status_code < 200 or status_code >= 300:
        return ProviderResult(media_id, "provider_error", "error", "http_error", None, {}, retry_after_seconds)
    if not isinstance(payload, dict):
        return ProviderResult(media_id, "schema_changed", "error", "schema_changed", None, {}, retry_after_seconds)

    header = payload.get("header")
    if not isinstance(header, dict):
        return ProviderResult(media_id, "schema_changed", "error", "schema_changed", None, {}, retry_after_seconds)
    header_summary = _saucenao_header_summary(header)
    status_value = header_summary.get("status")
    message = str(header.get("message") or header.get("long_message") or header.get("status_message") or header.get("error") or "").lower()
    if status_value is not None and int(status_value) > 0:
        result_class = "provider_error"
        error_class = "provider_error"
        if "unavailable" in message or "maintenance" in message or "service" in message:
            result_class = "provider_unavailable"
            error_class = "provider_unavailable"
        return ProviderResult(
            media_id,
            result_class,
            "error",
            error_class,
            None,
            {"saucenao_header": header_summary},
            retry_after_seconds,
        )
    if status_value is not None and int(status_value) < 0:
        result_class = "provider_error"
        error_class = "client_error"
        short_remaining = header_summary.get("short_remaining")
        long_remaining = header_summary.get("long_remaining")
        if long_remaining == 0:
            result_class = "quota_daily_exhausted"
            error_class = "quota_daily_exhausted"
        elif short_remaining == 0:
            result_class = "quota_short_exhausted"
            error_class = "quota_short_exhausted"
        elif "search" in message and ("out" in message or "limit" in message or "quota" in message):
            result_class = "out_of_searches"
            error_class = "out_of_searches"
        elif "limit" in message or "quota" in message:
            result_class = "rate_limited"
            error_class = "rate_limited"
        elif "api" in message and ("key" in message or "auth" in message):
            result_class = "auth_failed"
            error_class = "auth_failed"
        elif "forbidden" in message or "denied" in message:
            result_class = "forbidden"
            error_class = "forbidden"
        elif "bad" in message and "image" in message:
            result_class = "bad_image"
            error_class = "bad_image"
        return ProviderResult(
            media_id,
            result_class,
            "error",
            error_class,
            None,
            {"saucenao_header": header_summary},
            retry_after_seconds,
        )

    results = payload.get("results")
    if not isinstance(results, list):
        return ProviderResult(media_id, "schema_changed", "error", "schema_changed", None, {}, retry_after_seconds)
    if not results:
        return ProviderResult(
            media_id,
            "no_match",
            "no_match",
            None,
            None,
            {"result_count": 0, "saucenao_header": header_summary},
            retry_after_seconds,
        )

    normalized_entries = [
        _normalize_result_entry(item) for item in results if isinstance(item, dict)
    ]
    normalized_entries = [item for item in normalized_entries if item.get("similarity") is not None]
    if not normalized_entries:
        return ProviderResult(media_id, "schema_changed", "error", "schema_changed", None, {}, retry_after_seconds)
    normalized_entries.sort(key=lambda item: float(item["similarity"]), reverse=True)
    top = normalized_entries[0]
    score = float(top["similarity"])
    minimum_similarity = header_summary.get("minimum_similarity")
    top_has_source = bool(top.get("source_url_present") or top.get("result_id"))
    result_class = "low_confidence_match"
    if score >= HIGH_CONFIDENCE_THRESHOLD and top_has_source:
        result_class = "high_confidence_match"
    if minimum_similarity is not None and score < float(minimum_similarity):
        result_class = "low_confidence_match"
    if len(normalized_entries) > 1:
        second = normalized_entries[1]
        second_score = float(second["similarity"])
        different_source = (
            second.get("source_url_host")
            and top.get("source_url_host")
            and second.get("source_url_host") != top.get("source_url_host")
        )
        if (
            score >= HIGH_CONFIDENCE_THRESHOLD
            and second_score >= HIGH_CONFIDENCE_THRESHOLD
            and abs(score - second_score) <= CONFLICT_DELTA_THRESHOLD
            and different_source
        ):
            result_class = "conflict"

    if score < LOW_CONFIDENCE_THRESHOLD:
        result_class = "low_confidence_match"

    return ProviderResult(
        media_id=media_id,
        result_class=result_class,
        response_status="ok",
        error_class=None,
        score=score,
        normalized_payload={
            "result_count": len(results),
            "saucenao_header": header_summary,
            "top_result": top,
            "top_results": normalized_entries[:3],
            "privacy_redacted": True,
        },
        retry_after_seconds=retry_after_seconds,
    )


def run_saucenao_request(
    *,
    api_key: str,
    derived: DerivedInput,
    http_post: Callable[..., httpx.Response] | None = None,
) -> ProviderResult:
    request = build_saucenao_request(api_key, derived)
    post = http_post or httpx.post
    try:
        response = post(
            request["url"],
            params=request["params"],
            files=request["files"],
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
    except httpx.TimeoutException:
        return ProviderResult(derived.media_id, "provider_error", "error", "timeout", None, {}, None)
    except httpx.TransportError:
        return ProviderResult(derived.media_id, "provider_error", "error", "network_error", None, {}, None)
    try:
        payload = response.json()
    except ValueError:
        payload = None
    return classify_saucenao_response(
        media_id=derived.media_id,
        status_code=response.status_code,
        headers=dict(response.headers),
        payload=payload,
    )


def provider_selection_summary() -> dict[str, Any]:
    return {
        "selected_provider": PROVIDER_KEY,
        "provider_category": PROVIDER_CATEGORY,
        "why_selected": [
            "official site identifies SauceNAO as a reverse image search engine",
            "official index list includes anime illustration sources such as pixiv, danbooru, gelbooru, yande.re, anime-pictures, twitter, and skeb",
            "the current library is treated as no-source, so exact-source booru APIs are second-step verifiers rather than first provider",
            "trace.moe is better suited to anime scene screenshots than general illustration source discovery",
            "IQDB exposes a web upload form but no stable official automation API was verified for this pilot",
        ],
        "official_research_urls": [
            "https://saucenao.com/about.html",
            "https://saucenao.com/legal.html",
            "https://saucenao.com/",
            "https://saucenao.com/options.php",
            "https://saucenao.com/tools/examples/api/index_details.txt",
            "https://saucenao.com/user.php?page=search-api",
            "https://iqdb.org/",
            "https://soruly.github.io/trace.moe-api/",
            "https://gelbooru.com/index.php?page=help&topic=dapi",
            "https://anilist.gitbook.io/anilist-apiv2-docs/docs/guide/rate-limiting",
        ],
        "api_reference_status": "official_search_api_entrypoint_known_but_automated_fetch_returned_403",
        "terms_status": "official_legal_page_reviewed",
        "rate_limit_status": "account_based_limits_noted_from_official_terms; live execution requires local credential and provider-docs-verified flag",
    }


def validate_no_active_server_preflight(preflight: dict[str, Any]) -> None:
    issues: list[str] = []
    result = str(preflight.get("result", "")).strip().lower()
    backend = str(preflight.get("listener_backend", "")).strip().lower()
    if result != "clean":
        issues.append(f"result={result or 'missing'}")
    if backend not in ACCEPTABLE_SERVER_PREFLIGHT_BACKENDS:
        issues.append(f"listener_backend={backend or 'missing'}")
    for field in ("occupied_count", "confirmed_violet_count", "suspected_violet_count"):
        try:
            count = int(preflight.get(field))
        except (TypeError, ValueError):
            issues.append(f"{field}=missing_or_invalid")
            continue
        if count != 0:
            issues.append(f"{field}={count}")
    if issues:
        raise ServerPreflightBlockedError(
            "server_preflight_blocked: no-active-server preflight must be clean before Phase 4.4-B1 DB/storage/provider work; "
            + "; ".join(issues)
        )


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


def build_sample_gate(
    db: Session,
    *,
    media_ids: list[int],
    storage_root: Path,
) -> tuple[dict[str, Any], dict[int, Media]]:
    rows = db.query(Media).filter(Media.id.in_(media_ids)).all()
    by_id = {int(row.id): row for row in rows}
    request_plan: list[dict[str, Any]] = []
    details_rows: list[dict[str, Any]] = []
    content_distribution: Counter[str] = Counter()
    blocked_reasons: Counter[str] = Counter()

    for media_id in media_ids:
        media = by_id.get(media_id)
        if media is None:
            blocked_reasons["missing_media"] += 1
            content_distribution["missing"] += 1
            request_shape, query_hash = make_request_shape(media_id=media_id, content_class="missing")
            request_plan.append(
                {
                    "media_id": media_id,
                    "content_class": "missing",
                    "eligibility_status": "blocked",
                    "blocked_reason": "missing_media",
                    "request_shape_redacted": request_shape,
                    "query_hash": query_hash,
                    "would_send_original": False,
                    "would_send_thumbnail": False,
                    "would_send_derived_image": False,
                    "local_path_included": False,
                    "filename_included": False,
                    "source_label_included": False,
                }
            )
            details_rows.append({"media_id": media_id, "found": False, "blocked_reason": "missing_media"})
            continue

        content_class = _content_class_label(media.content_class)
        original = inspect_storage_path(storage_root, media.path, expected_kind="app_managed_original")
        thumbnail = inspect_storage_path(
            storage_root,
            media.thumbnail_path,
            expected_kind="app_managed_thumbnail",
            read_dimensions=True,
        )
        reason = _blocked_reason_for_media(media, original, thumbnail)
        status = "blocked" if reason else "eligible"
        if reason:
            blocked_reasons[reason] += 1
        content_distribution[content_class] += 1
        request_shape, query_hash = make_request_shape(media_id=media_id, content_class=content_class)
        request_plan.append(
            {
                "media_id": media_id,
                "content_class": content_class,
                "eligibility_status": status,
                "blocked_reason": reason,
                "request_shape_redacted": request_shape,
                "query_hash": query_hash,
                "input_kind": INPUT_KIND,
                "would_send_original": False,
                "would_send_thumbnail": False,
                "would_send_derived_image": status == "eligible",
                "local_path_included": False,
                "filename_included": False,
                "source_label_included": False,
            }
        )
        details_rows.append(
            {
                "media_id": int(media.id),
                "found": True,
                "file_type": _file_type_label(media.file_type),
                "content_class": content_class,
                "eligibility_status": status,
                "blocked_reason": reason,
                "original_status": original.status,
                "thumbnail_status": thumbnail.status,
                "thumbnail_dimensions": thumbnail.dimensions,
                "source_field_read_for_fallback": False,
            }
        )

    eligible_count = sum(1 for row in request_plan if row["eligibility_status"] == "eligible")
    blocked_count = len(request_plan) - eligible_count
    sample_gate = {
        "approved_sample_media_ids": list(APPROVED_SAMPLE_IDS),
        "requested_media_ids": media_ids,
        "approved_sample_count": len(APPROVED_SAMPLE_IDS),
        "requested_count": len(media_ids),
        "found_media_count": len(by_id),
        "eligible_count": eligible_count,
        "blocked_count": blocked_count,
        "blocked_count_by_reason": dict(sorted(blocked_reasons.items())),
        "content_class_distribution": dict(sorted(content_distribution.items())),
        "request_plan": request_plan,
        "sample_details": details_rows,
    }
    return sample_gate, by_id


def _credential_status(api_key: str | None) -> dict[str, Any]:
    return {
        "provider_requires_api_key": True,
        "credential_name": "SAUCENAO_API_KEY",
        "present": bool(api_key),
        "value_printed": False,
        "included_in_public_report": False,
        "included_in_local_details": False,
    }


def _setup_instructions() -> dict[str, Any]:
    return {
        "required_credential_name": "SAUCENAO_API_KEY",
        "configure_process_env": '$env:SAUCENAO_API_KEY = "<your SauceNAO API key>"',
        "configure_local_env_file": "Add SAUCENAO_API_KEY=<your SauceNAO API key> to .env (do not commit .env).",
        "verify_without_printing_secret": (
            "$envHas = [bool]$env:SAUCENAO_API_KEY; "
            '$dotenvHas = [bool](Select-String -Path .env -SimpleMatch "SAUCENAO_API_KEY=" -Quiet); '
            "[pscustomobject]@{SAUCENAO_API_KEY_present=($envHas -or $dotenvHas)}"
        ),
        "rerun_command": (
            '& "$PY" scripts/run_phase44b1_one_provider_live_reverse_search_pilot.py '
            "--media-ids 2690 2687 2670 2654 2647 "
            "--execute-live --upload-derived-approved --provider-docs-verified "
            "--report-json docs/reports/phase-4.4b1-one-provider-live-reverse-search-pilot-summary.json "
            "--report-md docs/reports/phase-4.4b1-one-provider-live-reverse-search-pilot.md "
            "--local-details-json .local_manifests/phase-4.4b1-live-details.json "
            "--derived-dir .local_manifests/phase-4.4b1-derived "
            "--no-active-server-preflight-result clean "
            "--no-active-server-listener-backend windows_netstat "
            "--no-active-server-occupied-count 0 "
            "--no-active-server-confirmed-violet-count 0 "
            "--no-active-server-suspected-violet-count 0"
        ),
    }


def build_base_summary(
    *,
    generated_at: str,
    no_active_server_preflight: dict[str, Any],
    identity: dict[str, Any],
    sample_gate: dict[str, Any],
    credential_status: dict[str, Any],
    execute_live: bool,
    upload_derived_approved: bool,
    provider_docs_verified: bool,
    write_db_records: bool,
) -> dict[str, Any]:
    return {
        "phase": PHASE,
        "lifecycle": "phase-scoped operational runner",
        "generated_at": generated_at,
        "status": "not_started",
        "stop_condition": None,
        "provider_selection": provider_selection_summary(),
        "credential_status": credential_status,
        "setup_instructions": _setup_instructions() if not credential_status["present"] else None,
        "upload_approval_scope": {
            "user_approved_derived_resized_stripped_upload_for_approved_ids_only": upload_derived_approved,
            "approved_media_ids": list(APPROVED_SAMPLE_IDS),
            "original_upload_allowed": False,
            "thumbnail_upload_allowed": False,
            "unknown_upload_allowed": False,
            "non_anime_upload_allowed": False,
            "unapproved_illustration_upload_allowed": False,
            "full_library_expansion_allowed": False,
        },
        "execution_flags": {
            "execute_live_requested": execute_live,
            "provider_docs_verified_by_operator": provider_docs_verified,
            "write_db_records_requested": write_db_records,
        },
        "closeout_hardening": {
            "credential_required_remains_current_stop_condition": not credential_status["present"],
            "rerun_command_includes_no_active_server_preflight_args": True,
            "partial_live_run_accounting_preserves_attempted_items": True,
            "partial_live_run_status": "partial_run_stopped",
            "db_writes_deferred_until_first_live_behavior_validation": True,
        },
        "no_active_server_preflight": no_active_server_preflight,
        "identity": identity,
        "sample_gate": {
            key: value for key, value in sample_gate.items() if key not in {"sample_details"}
        },
        "input_policy": {
            "input_kind": INPUT_KIND,
            "transform_policy_version": TRANSFORM_POLICY_VERSION,
            "max_derived_dimension": MAX_DERIVED_DIMENSION,
            "source_preference": "app_managed_original_derived_when_available_else_thumbnail",
            "original_upload": False,
            "thumbnail_upload": False,
            "derived_upload": bool(execute_live and upload_derived_approved and credential_status["present"]),
            "local_paths_in_request": False,
            "filenames_in_request": False,
            "source_labels_in_request": False,
        },
        "request_budget": {
            "max_items": len(APPROVED_SAMPLE_IDS),
            "max_requests": len(APPROVED_SAMPLE_IDS),
            "requests_per_minute": REQUESTS_PER_MINUTE,
            "concurrency": 1,
            "max_failures": MAX_FAILURES,
            "max_consecutive_failures": MAX_CONSECUTIVE_FAILURES,
            "max_same_reason_failures": MAX_SAME_REASON_FAILURES,
            "retries": 0,
            "stop_conditions": [
                "credential_required",
                "provider_policy_blocked",
                "sample_gate_blocked",
                "auth_failed",
                "forbidden",
                "rate_limited",
                "schema_changed",
                "privacy_leak",
                "unexpected_mutation",
                "failure_budget_exceeded",
                "user_abort",
            ],
        },
        "derived_inputs": {
            "generated_count": 0,
            "uploaded_count": 0,
            "source_kind_counts": {},
            "public_hashes_included": False,
            "local_paths_included": False,
            "safe_artifact_ids": [],
        },
        "live_requests": {
            "attempted": 0,
            "skipped": len(APPROVED_SAMPLE_IDS),
            "provider": PROVIDER_KEY,
            "concurrency": 1,
            "partial_run_stopped": False,
            "stop_reason": None,
        },
        "provider_results_by_class": {},
        "live_request_items": [],
        "quota_observations": {
            "header_status_values": [],
            "short_remaining_values": [],
            "long_remaining_values": [],
            "minimum_similarity_values": [],
            "short_quota_exhausted": False,
            "daily_quota_exhausted": False,
            "out_of_searches": False,
            "provider_availability": "not_evaluated_without_live_results",
            "total_wait_seconds": 0,
        },
        "db_writes": {
            "attempted": False,
            "backup_required": False,
            "deferred_until_provider_pilot_validated": True,
            "restore_recovery_note": "No DB writes were attempted.",
            "by_table": {table: 0 for table in sorted(ALLOWED_WRITE_TABLES)},
            "forbidden_tables_written": {table: 0 for table in FORBIDDEN_WRITE_TABLES},
        },
        "evidence_candidate_behavior": {
            "entity_evidence_created": 0,
            "media_entity_candidates_created": 0,
            "confirmed_assignments_created": 0,
            "candidate_policy": "evidence_only_until high-confidence result mapping is manually reviewed",
            "automatic_trusted_entity_creation": False,
        },
        "privacy_scan": {
            "passed": False,
            "checked_public_artifacts": [],
        },
        "safety_confirmation": {
            "original_upload": False,
            "unknown_non_anime_unapproved_illustration_upload": False,
            "full_library_scan": False,
            "source_icloud_mutation": False,
            "app_managed_storage_mutation": False,
            "db_import": False,
            "classification": False,
            "ai_tagging": False,
            "localization": False,
            "entity_resolver": False,
            "similarity_clustering": False,
            "confirmed_assignments": False,
            "media_tags_mutation": False,
            "tag_translation_mutation": False,
            "api_key_exposed": False,
            "browser_automation_against_provider": False,
            "scraping": False,
        },
        "local_artifacts": {
            "details_json": "phase-4.4b1 local details under .local_manifests",
            "derived_inputs": "none_generated_until_live_gates_pass",
            "ignored_by_gitignore": True,
            "absolute_paths_in_public_report": False,
        },
        "manual_review_burden_estimate": {
            "high_confidence_matches": 0,
            "conflicts": 0,
            "estimated_targeted_review_items": 0,
        },
        "larger_pilot_suitability": {
            "suitable_for_larger_pilot": False,
            "reason": "not_evaluated_without_live_results",
            "phase39_required_before_scaling": True,
        },
        "subscription_analysis": {
            "recommended_now": False,
            "reason": "not_evaluated_without_live_results",
            "likely_solves": "quota_or_throughput_only_not_match_quality",
            "purchase_or_subscription_performed": False,
        },
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


def _details_derived_row(derived: DerivedInput) -> dict[str, Any]:
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


def _update_summary_for_stop(summary: dict[str, Any], *, status: str, stop_condition: str) -> None:
    summary["status"] = status
    summary["stop_condition"] = stop_condition
    summary["live_requests"]["stop_reason"] = stop_condition
    if stop_condition == "credential_required":
        summary["live_requests"]["attempted"] = 0
        summary["live_requests"]["skipped"] = summary["sample_gate"]["requested_count"]
    if stop_condition == "sample_gate_blocked":
        summary["live_requests"]["attempted"] = 0
        summary["live_requests"]["skipped"] = summary["sample_gate"]["requested_count"]


def _live_request_items(
    sample_gate: dict[str, Any],
    public_results: list[dict[str, Any]],
    *,
    partial_stop_condition: str | None,
) -> list[dict[str, Any]]:
    results_by_id = {int(row["media_id"]): row for row in public_results}
    items: list[dict[str, Any]] = []
    for row in sample_gate["request_plan"]:
        media_id = int(row["media_id"])
        if row["eligibility_status"] != "eligible":
            items.append(
                {
                    "media_id": media_id,
                    "final_state": "skipped_ineligible",
                    "blocked_reason": row["blocked_reason"],
                    "request_attempted": False,
                    "derived_input_generated": False,
                }
            )
            continue
        result = results_by_id.get(media_id)
        if result is not None:
            items.append(
                {
                    "media_id": media_id,
                    "final_state": result["result_class"],
                    "request_attempted": True,
                    "derived_input_generated": True,
                    "upload_attempted": True,
                    "score": result.get("score"),
                    "error_class": result.get("error_class"),
                }
            )
            continue
        items.append(
            {
                "media_id": media_id,
                "final_state": "skipped_due_to_stop" if partial_stop_condition else "skipped",
                "stop_reason": partial_stop_condition,
                "request_attempted": False,
                "derived_input_generated": False,
                "upload_attempted": False,
            }
        )
    return items


def _quota_observations(
    public_results: list[dict[str, Any]],
    *,
    total_wait_seconds: int,
) -> dict[str, Any]:
    headers = [
        row.get("saucenao_header")
        for row in public_results
        if isinstance(row.get("saucenao_header"), dict)
    ]
    result_classes = {str(row.get("result_class")) for row in public_results}
    provider_availability = "not_evaluated_without_live_results"
    if public_results:
        provider_availability = "available"
    if result_classes & {"provider_unavailable", "provider_error"}:
        provider_availability = "provider_error_or_unavailable"
    if result_classes & {"auth_failed", "forbidden"}:
        provider_availability = "credential_or_permission_blocked"
    if result_classes & {"quota_short_exhausted", "quota_daily_exhausted", "out_of_searches", "rate_limited"}:
        provider_availability = "quota_or_rate_limited"
    return {
        "header_status_values": [header.get("status") for header in headers],
        "short_remaining_values": [header.get("short_remaining") for header in headers],
        "long_remaining_values": [header.get("long_remaining") for header in headers],
        "minimum_similarity_values": [header.get("minimum_similarity") for header in headers],
        "short_quota_exhausted": any(
            row.get("result_class") == "quota_short_exhausted"
            or (isinstance(row.get("saucenao_header"), dict) and row["saucenao_header"].get("short_remaining") == 0)
            for row in public_results
        ),
        "daily_quota_exhausted": any(
            row.get("result_class") == "quota_daily_exhausted"
            or (isinstance(row.get("saucenao_header"), dict) and row["saucenao_header"].get("long_remaining") == 0)
            for row in public_results
        ),
        "out_of_searches": "out_of_searches" in result_classes,
        "provider_availability": provider_availability,
        "total_wait_seconds": total_wait_seconds,
    }


def _apply_live_execution_state(
    summary: dict[str, Any],
    details: dict[str, Any],
    *,
    sample_gate: dict[str, Any],
    public_results: list[dict[str, Any]],
    detail_results: list[dict[str, Any]],
    result_counts: dict[str, int],
    db_counts: dict[str, int],
    status: str,
    stop_condition: str | None,
    total_wait_seconds: int = 0,
) -> None:
    attempted_count = len(public_results)
    summary["status"] = status
    summary["stop_condition"] = stop_condition
    summary["live_requests"]["attempted"] = attempted_count
    summary["live_requests"]["skipped"] = max(0, sample_gate["eligible_count"] - attempted_count)
    summary["live_requests"]["partial_run_stopped"] = status == "partial_run_stopped"
    summary["live_requests"]["stop_reason"] = stop_condition
    summary["provider_results_by_class"] = result_counts
    summary["provider_result_items"] = public_results
    summary["quota_observations"] = _quota_observations(public_results, total_wait_seconds=total_wait_seconds)
    summary["live_request_items"] = _live_request_items(
        sample_gate,
        public_results,
        partial_stop_condition=stop_condition if status == "partial_run_stopped" else None,
    )
    summary["derived_inputs"]["generated_count"] = len(detail_results)
    summary["derived_inputs"]["uploaded_count"] = attempted_count
    summary["derived_inputs"]["source_kind_counts"] = dict(Counter(row["derived"]["source_kind"] for row in detail_results))
    summary["derived_inputs"]["safe_artifact_ids"] = [row["derived"]["artifact_id"] for row in detail_results]
    summary["local_artifacts"]["derived_inputs"] = (
        "generated_safe_derived_files_under_.local_manifests" if detail_results else "none_generated"
    )
    summary["db_writes"]["attempted"] = False
    summary["db_writes"]["backup_required"] = False
    summary["db_writes"]["restore_recovery_note"] = (
        "DB writes are deferred until the first live provider behavior validation is reviewed."
    )
    summary["db_writes"]["by_table"] = db_counts
    summary["evidence_candidate_behavior"]["entity_evidence_created"] = db_counts.get("EntityEvidence", 0)
    summary["evidence_candidate_behavior"]["media_entity_candidates_created"] = db_counts.get("MediaEntityCandidate", 0)
    summary["evidence_candidate_behavior"]["confirmed_assignments_created"] = 0
    details["provider_results"] = detail_results
    details["derived_inputs"] = [row["derived"] for row in detail_results]


def write_db_records_for_result(
    db: Session,
    *,
    media_id: int,
    query_hash: str,
    request_shape_redacted: dict[str, Any],
    result: ProviderResult,
) -> dict[str, int]:
    counts = {table: 0 for table in sorted(ALLOWED_WRITE_TABLES)}
    expires_at = _now() + timedelta(days=30)

    cache = (
        db.query(ProviderCache)
        .filter(
            ProviderCache.provider == PROVIDER_KEY,
            ProviderCache.query_hash == query_hash,
            ProviderCache.query_type == QUERY_TYPE,
        )
        .first()
    )
    if cache is None:
        cache = ProviderCache(
            provider=PROVIDER_KEY,
            query_hash=query_hash,
            query_type=QUERY_TYPE,
            request_shape_redacted=request_shape_redacted,
            response_status=result.response_status,
            response_json_redacted=result.normalized_payload,
            error_class=result.error_class,
            expires_at=expires_at,
        )
        db.add(cache)
        counts["ProviderCache"] += 1
    else:
        cache.request_shape_redacted = request_shape_redacted
        cache.response_status = result.response_status
        cache.response_json_redacted = result.normalized_payload
        cache.error_class = result.error_class
        cache.fetched_at = _now()
        cache.expires_at = expires_at
        counts["ProviderCache"] += 1
    db.flush()

    if result.result_class in {"no_match", "low_confidence_match", "privacy_blocked"}:
        negative = (
            db.query(NegativeLookupCache)
            .filter(
                NegativeLookupCache.provider == PROVIDER_KEY,
                NegativeLookupCache.query_hash == query_hash,
                NegativeLookupCache.query_type == QUERY_TYPE,
            )
            .first()
        )
        if negative is None:
            db.add(
                NegativeLookupCache(
                    provider=PROVIDER_KEY,
                    query_hash=query_hash,
                    query_type=QUERY_TYPE,
                    reason=result.result_class,
                    expires_at=expires_at,
                )
            )
        else:
            negative.reason = result.result_class
            negative.expires_at = expires_at
        counts["NegativeLookupCache"] += 1

    if result.result_class in {"high_confidence_match", "low_confidence_match", "conflict"}:
        score = result.score / 100.0 if result.score is not None else None
        evidence = record_evidence(
            db,
            evidence_type=EntityEvidenceTypeEnum.reverse_search,
            source_type="external",
            provider=PROVIDER_KEY,
            media_id=media_id,
            query_hash=query_hash,
            payload_ref=f"ProviderCache:{PROVIDER_KEY}:{query_hash}",
            score=score,
            summary=f"Redacted SauceNAO reverse-search {result.result_class} for approved media {media_id}",
            privacy_redacted=True,
            observed_at=_now(),
        )
        counts["EntityEvidence"] += 1
        # Candidate creation remains disabled by default in B1 because provider
        # results are source evidence, not a reliable entity taxonomy.
        _ = evidence

    return counts


def _merge_counts(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        target[key] = int(target.get(key, 0)) + int(value)


def execute_live_requests(
    db: Session,
    *,
    media_by_id: dict[int, Media],
    sample_gate: dict[str, Any],
    storage_root: Path,
    derived_dir: Path,
    api_key: str,
    write_db_records: bool,
    http_post: Callable[..., httpx.Response] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int], dict[str, int]]:
    if write_db_records:
        raise ProviderPolicyBlocked("db_writes_deferred_until_provider_pilot_validated")
    details_results: list[dict[str, Any]] = []
    public_results: list[dict[str, Any]] = []
    result_classes: Counter[str] = Counter()
    db_counts = {table: 0 for table in sorted(ALLOWED_WRITE_TABLES)}
    failures = 0
    consecutive_failures = 0
    same_reason_failures: Counter[str] = Counter()
    delay_seconds = max(0.0, 60.0 / REQUESTS_PER_MINUTE)
    total_wait_seconds = 0

    eligible_ids = [
        int(row["media_id"])
        for row in sample_gate["request_plan"]
        if row["eligibility_status"] == "eligible"
    ]
    for index, media_id in enumerate(eligible_ids):
        media = media_by_id[media_id]
        derived = generate_derived_input(media, storage_root=storage_root, output_dir=derived_dir)
        request_shape, query_hash = make_request_shape(
            media_id=media_id,
            content_class=_content_class_label(media.content_class),
            derived_sha256=derived.sha256,
        )
        assert_public_payload_safe(
            {
                "request_shape": json.dumps(request_shape, ensure_ascii=False),
                "derived_public": json.dumps(_public_derived_row(derived), ensure_ascii=False),
            }
        )
        result = run_saucenao_request(api_key=api_key, derived=derived, http_post=http_post)
        result_classes[result.result_class] += 1
        saucenao_header = result.normalized_payload.get("saucenao_header") if isinstance(result.normalized_payload, dict) else None
        if not isinstance(saucenao_header, dict):
            saucenao_header = {}
        public_results.append(
            {
                "media_id": media_id,
                "result_class": result.result_class,
                "score": result.score,
                "error_class": result.error_class,
                "saucenao_header": saucenao_header,
                "source_url_present": bool(result.normalized_payload.get("top_result", {}).get("source_url_present")),
                "source_url_host": result.normalized_payload.get("top_result", {}).get("source_url_host"),
                "raw_payload_included": False,
            }
        )
        details_results.append(
            {
                "media_id": media_id,
                "derived": _details_derived_row(derived),
                "query_hash": query_hash,
                "request_shape_redacted": request_shape,
                "provider_result": {
                    "result_class": result.result_class,
                    "response_status": result.response_status,
                    "error_class": result.error_class,
                    "score": result.score,
                    "normalized_payload": result.normalized_payload,
                    "raw_payload_included": False,
                },
            }
        )

        stop_classes = {
            "auth_failed",
            "forbidden",
            "rate_limited",
            "schema_changed",
            "quota_short_exhausted",
            "quota_daily_exhausted",
            "out_of_searches",
            "provider_unavailable",
        }
        if result.result_class in stop_classes:
            raise ProviderStop(
                result.result_class,
                public_results=public_results,
                details_results=details_results,
                result_counts=dict(result_classes),
                db_counts=db_counts,
                total_wait_seconds=total_wait_seconds,
            )
        if result.result_class in {"provider_error", "privacy_blocked", "bad_image"}:
            failures += 1
            consecutive_failures += 1
            same_reason_failures[result.error_class or result.result_class] += 1
        else:
            consecutive_failures = 0
        if failures > MAX_FAILURES or consecutive_failures > MAX_CONSECUTIVE_FAILURES:
            raise ProviderStop(
                "failure_budget_exceeded",
                public_results=public_results,
                details_results=details_results,
                result_counts=dict(result_classes),
                db_counts=db_counts,
                total_wait_seconds=total_wait_seconds,
            )
        if any(count > MAX_SAME_REASON_FAILURES for count in same_reason_failures.values()):
            raise ProviderStop(
                "same_reason_failure_budget_exceeded",
                public_results=public_results,
                details_results=details_results,
                result_counts=dict(result_classes),
                db_counts=db_counts,
                total_wait_seconds=total_wait_seconds,
            )

        if write_db_records:
            with db.begin_nested():
                _merge_counts(
                    db_counts,
                    write_db_records_for_result(
                        db,
                        media_id=media_id,
                        query_hash=query_hash,
                        request_shape_redacted=request_shape,
                        result=result,
                    ),
                )

        if index < len(eligible_ids) - 1:
            short_remaining = saucenao_header.get("short_remaining")
            long_remaining = saucenao_header.get("long_remaining")
            if long_remaining == 0:
                raise ProviderStop(
                    "quota_daily_exhausted",
                    public_results=public_results,
                    details_results=details_results,
                    result_counts=dict(result_classes),
                    db_counts=db_counts,
                    total_wait_seconds=total_wait_seconds,
                )
            if short_remaining == 0:
                wait_seconds = SHORT_QUOTA_WAIT_SECONDS
                if total_wait_seconds + wait_seconds > MAX_TOTAL_QUOTA_WAIT_SECONDS:
                    raise ProviderStop(
                        "quota_short_exhausted",
                        public_results=public_results,
                        details_results=details_results,
                        result_counts=dict(result_classes),
                        db_counts=db_counts,
                        total_wait_seconds=total_wait_seconds,
                    )
                time.sleep(wait_seconds)
                total_wait_seconds += wait_seconds
            else:
                time.sleep(delay_seconds)
                total_wait_seconds += int(delay_seconds)

    db_counts["__total_wait_seconds"] = total_wait_seconds
    return public_results, details_results, dict(result_classes), db_counts


WINDOWS_PATH_RE = re.compile(r"(?i)(^|[^A-Za-z0-9_])([A-Z]:[\\/])")
UNC_PATH_RE = re.compile(r"(^|[^A-Za-z0-9_:])(\\\\|//)")
FILE_URL_RE = re.compile(r"(?i)\bfile://")
NAS_RE = re.compile(r"(?i)(Z:[\\/]|\\\\192\.168\.71\.230\\Storage|//192\.168\.71\.230/Storage)")
SECRET_RE = re.compile(r"(?i)\b(Bearer\s+[A-Za-z0-9._~+\-/]{16,}|sk[-_](live|test)[-_][A-Za-z0-9_-]{16,})")


def public_privacy_issues(text_payload: str) -> list[str]:
    issues = []
    for label, pattern in (
        ("windows_absolute_path", WINDOWS_PATH_RE),
        ("unc_or_network_path", UNC_PATH_RE),
        ("file_url", FILE_URL_RE),
        ("nas_path", NAS_RE),
        ("secret_shaped_token", SECRET_RE),
    ):
        if pattern.search(text_payload):
            issues.append(label)
    return issues


def assert_public_payload_safe(payloads: dict[str, str]) -> None:
    all_issues = {label: public_privacy_issues(payload) for label, payload in payloads.items()}
    all_issues = {label: issues for label, issues in all_issues.items() if issues}
    if all_issues:
        raise Phase44B1Error(f"privacy_scan_failed: {all_issues}")


def build_markdown_report(summary: dict[str, Any]) -> str:
    sample = summary["sample_gate"]
    result_lines = "\n".join(f"- `{key}`: `{value}`" for key, value in sorted(summary["provider_results_by_class"].items())) or "- none"
    db_lines = "\n".join(f"- `{key}`: `{value}`" for key, value in sorted(summary["db_writes"]["by_table"].items()))
    blocked_lines = "\n".join(f"- `{key}`: `{value}`" for key, value in sample["blocked_count_by_reason"].items()) or "- none"
    live_item_lines = "\n".join(
        f"- media `{row['media_id']}`: `{row['final_state']}`"
        for row in summary.get("live_request_items", [])
    ) or "- none"
    setup = summary.get("setup_instructions") or {}
    quota = summary.get("quota_observations", {})
    setup_lines = ""
    if setup:
        setup_lines = f"""
## Credential Setup

- Required credential: `{setup['required_credential_name']}`
- Process env: `{setup['configure_process_env']}`
- Local `.env`: `{setup['configure_local_env_file']}`
- Verify without printing secret: `{setup['verify_without_printing_secret']}`
- Next rerun command: `{setup['rerun_command']}`
"""
    return f"""# Phase 4.4-B1 - SauceNAO Live Rerun Results

Date: {summary['generated_at']}

## Summary

- Status: `{summary['status']}`
- Stop condition: `{summary.get('stop_condition') or 'N/A'}`
- Provider selected: `{summary['provider_selection']['selected_provider']}`
- Provider category: `{summary['provider_selection']['provider_category']}`
- Credential present: `{summary['credential_status']['present']}`
- Live requests attempted: `{summary['live_requests']['attempted']}`
- Derived inputs generated: `{summary['derived_inputs']['generated_count']}`
- DB writes attempted: `{summary['db_writes']['attempted']}`
- Confirmed assignments created: `{summary['evidence_candidate_behavior']['confirmed_assignments_created']}`

## Closeout Hardening

- Credential-required remains current stop condition: `{summary['closeout_hardening']['credential_required_remains_current_stop_condition']}`
- Rerun command includes no-active-server preflight args: `{summary['closeout_hardening']['rerun_command_includes_no_active_server_preflight_args']}`
- Partial live-run accounting preserves attempted items: `{summary['closeout_hardening']['partial_live_run_accounting_preserves_attempted_items']}`
- Mid-run provider stop status: `{summary['closeout_hardening']['partial_live_run_status']}`
- DB writes deferred until first live behavior validation: `{summary['closeout_hardening']['db_writes_deferred_until_first_live_behavior_validation']}`

## Provider Selection

Selected SauceNAO because it is an official reverse image search service with anime/illustration-relevant indexes and is the best fit for no-source anime illustration discovery. Exact booru APIs remain second-step verifiers after source discovery; trace.moe is screenshot-oriented; IQDB automation lacks a verified stable official API for this pilot.

- API reference status: `{summary['provider_selection']['api_reference_status']}`
- Terms status: `{summary['provider_selection']['terms_status']}`
- Rate-limit status: `{summary['provider_selection']['rate_limit_status']}`

## Approved Sample Gate

- Approved media IDs: `{', '.join(str(item) for item in summary['sample_gate']['approved_sample_media_ids'])}`
- Requested media IDs: `{', '.join(str(item) for item in summary['sample_gate']['requested_media_ids'])}`
- Found media count: `{sample['found_media_count']}`
- Eligible count: `{sample['eligible_count']}`
- Blocked count: `{sample['blocked_count']}`

Blocked reasons:

{blocked_lines}

## No-active-server Preflight

- Result: `{summary['no_active_server_preflight'].get('result')}`
- Listener backend: `{summary['no_active_server_preflight'].get('listener_backend')}`
- Occupied ports: `{summary['no_active_server_preflight'].get('occupied_count')}`
- Confirmed V.I.O.L.E.T. servers: `{summary['no_active_server_preflight'].get('confirmed_violet_count')}`
- Suspected V.I.O.L.E.T. servers: `{summary['no_active_server_preflight'].get('suspected_violet_count')}`

## DB / Storage Identity Proof

- `VIOLET_ENV`: `{summary['identity'].get('violet_env')}`
- Configured DB host: `{summary['identity'].get('configured_db_host')}`
- Configured DB port: `{summary['identity'].get('configured_db_port')}`
- Configured DB user: `{summary['identity'].get('configured_db_user')}`
- Configured DB name: `{summary['identity'].get('configured_db_name')}`
- Actual DB name: `{summary['identity'].get('actual_db_name')}`
- DB identity result: `{summary['identity'].get('db_identity_result')}`
- DB password included: `false`
- Storage root mode: `{summary['identity'].get('storage_root_mode')}`
- Local paths redacted: `{summary['identity'].get('local_paths_redacted')}`

## Derived Input Policy

- Input kind: `{summary['input_policy']['input_kind']}`
- Transform policy: `{summary['input_policy']['transform_policy_version']}`
- Max dimension: `{summary['input_policy']['max_derived_dimension']}`
- Original upload: `false`
- Thumbnail upload: `false`
- Derived upload attempted: `{summary['derived_inputs']['uploaded_count']}`
- Public hashes included: `false`

## Request Results

- Requests attempted: `{summary['live_requests']['attempted']}`
- Requests skipped: `{summary['live_requests']['skipped']}`
- Partial run stopped: `{summary['live_requests'].get('partial_run_stopped', False)}`
- Stop reason: `{summary['live_requests'].get('stop_reason') or 'N/A'}`
- SauceNAO header.status values: `{quota.get('header_status_values', [])}`
- SauceNAO short_remaining values: `{quota.get('short_remaining_values', [])}`
- SauceNAO long_remaining values: `{quota.get('long_remaining_values', [])}`
- SauceNAO minimum_similarity values: `{quota.get('minimum_similarity_values', [])}`
- Short quota exhausted: `{quota.get('short_quota_exhausted', False)}`
- Daily quota exhausted: `{quota.get('daily_quota_exhausted', False)}`
- Out of searches: `{quota.get('out_of_searches', False)}`
- Provider availability: `{quota.get('provider_availability', 'unknown')}`
- Total wait seconds: `{quota.get('total_wait_seconds', 0)}`

{result_lines}

Per-item final states:

{live_item_lines}

## DB Writes

- Attempted: `{summary['db_writes']['attempted']}`
- Deferred until provider pilot validated: `{summary['db_writes'].get('deferred_until_provider_pilot_validated', False)}`
- Restore/recovery note: `{summary['db_writes']['restore_recovery_note']}`

{db_lines}

## Evidence / Candidate Behavior

- EntityEvidence created: `{summary['evidence_candidate_behavior']['entity_evidence_created']}`
- MediaEntityCandidate created: `{summary['evidence_candidate_behavior']['media_entity_candidates_created']}`
- Confirmed assignments created: `0`
- Automatic trusted Entity creation: `false`

## Privacy Scan

- Passed: `{summary['privacy_scan']['passed']}`
- Public artifacts checked: `{', '.join(summary['privacy_scan']['checked_public_artifacts']) if summary['privacy_scan']['checked_public_artifacts'] else 'not yet recorded'}`
- Public report excludes API key, local paths, filenames, source labels, raw request payloads, raw image bytes, and unredacted provider payloads.
{setup_lines}
## Safety Confirmation

- No original upload.
- No unknown/non_anime/unapproved illustration upload.
- No full-library scan.
- No source/iCloud mutation.
- No app-managed storage mutation.
- No DB import.
- No classification.
- No AI tagging.
- No localization.
- No Entity Resolver.
- No similarity/clustering.
- No confirmed assignment.
- No media_tags mutation.
- No TagTranslation mutation.

## Decision

- Provider suitable for larger pilot: `{summary['larger_pilot_suitability']['suitable_for_larger_pilot']}`
- Reason: `{summary['larger_pilot_suitability']['reason']}`
- Phase 3.9 required before scaling: `true`
- Subscription recommended now: `{summary.get('subscription_analysis', {}).get('recommended_now', False)}`
- Subscription reason: `{summary.get('subscription_analysis', {}).get('reason', 'N/A')}`
- Subscription likely solves: `{summary.get('subscription_analysis', {}).get('likely_solves', 'N/A')}`
- Purchase/subscription performed: `{summary.get('subscription_analysis', {}).get('purchase_or_subscription_performed', False)}`
"""


def write_reports(summary: dict[str, Any], details: dict[str, Any], *, report_json: Path, report_md: Path, local_details_json: Path) -> None:
    markdown = build_markdown_report(summary)
    summary_text = json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True)
    assert_public_payload_safe({_public_path_label(report_json): summary_text, _public_path_label(report_md): markdown})
    summary["privacy_scan"] = {
        "passed": True,
        "checked_public_artifacts": [_public_path_label(report_json), _public_path_label(report_md)],
    }
    markdown = build_markdown_report(summary)
    summary_text = json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True)
    assert_public_payload_safe({_public_path_label(report_json): summary_text, _public_path_label(report_md): markdown})
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_md.parent.mkdir(parents=True, exist_ok=True)
    local_details_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(summary_text + "\n", encoding="utf-8")
    report_md.write_text(markdown, encoding="utf-8")
    local_details_json.write_text(json.dumps(details, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _server_preflight_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "result": args.no_active_server_preflight_result,
        "listener_backend": args.no_active_server_listener_backend,
        "occupied_count": args.no_active_server_occupied_count,
        "confirmed_violet_count": args.no_active_server_confirmed_violet_count,
        "suspected_violet_count": args.no_active_server_suspected_violet_count,
        "proof_required": True,
        "clean_required_before_db_access": True,
        "acceptable_listener_backends": sorted(ACCEPTABLE_SERVER_PREFLIGHT_BACKENDS),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media-ids", nargs="+", type=int, required=True)
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--upload-derived-approved", action="store_true")
    parser.add_argument("--provider-docs-verified", action="store_true")
    parser.add_argument("--write-db-records", action="store_true")
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--report-md", required=True)
    parser.add_argument("--local-details-json", required=True)
    parser.add_argument("--derived-dir", default=".local_manifests/phase-4.4b1-derived")
    parser.add_argument("--no-active-server-preflight-result", required=True)
    parser.add_argument("--no-active-server-listener-backend", required=True)
    parser.add_argument("--no-active-server-occupied-count", type=int, required=True)
    parser.add_argument("--no-active-server-confirmed-violet-count", type=int, required=True)
    parser.add_argument("--no-active-server-suspected-violet-count", type=int, required=True)
    return parser


def run(args: argparse.Namespace, *, http_post: Callable[..., httpx.Response] | None = None) -> dict[str, Any]:
    media_ids = parse_media_ids(args.media_ids)
    report_json = resolve_output_path(args.report_json, expected_parent=PUBLIC_REPORT_DIR)
    report_md = resolve_output_path(args.report_md, expected_parent=PUBLIC_REPORT_DIR)
    local_details_json = resolve_output_path(args.local_details_json, expected_parent=LOCAL_DETAILS_DIR)
    derived_dir = resolve_output_path(args.derived_dir, expected_parent=LOCAL_DETAILS_DIR)

    no_active_server_preflight = _server_preflight_from_args(args)
    validate_no_active_server_preflight(no_active_server_preflight)
    api_key = get_saucenao_api_key(ROOT)
    config = load_project_config(ROOT)
    engine = create_engine(config.database_url, pool_pre_ping=True)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    generated_at = _now_iso()
    summary: dict[str, Any] | None = None
    details: dict[str, Any] = {
        "phase": PHASE,
        "generated_at": generated_at,
        "absolute_paths_included": False,
        "api_key_included": False,
        "sample_details": [],
        "derived_inputs": [],
        "provider_results": [],
    }
    try:
        identity = prove_db_identity(session, config)
        sample_gate, media_by_id = build_sample_gate(session, media_ids=media_ids, storage_root=config.storage_root)
        details["sample_details"] = sample_gate["sample_details"]
        summary = build_base_summary(
            generated_at=generated_at,
            no_active_server_preflight=no_active_server_preflight,
            identity=identity,
            sample_gate=sample_gate,
            credential_status=_credential_status(api_key),
            execute_live=bool(args.execute_live),
            upload_derived_approved=bool(args.upload_derived_approved),
            provider_docs_verified=bool(args.provider_docs_verified),
            write_db_records=bool(args.write_db_records),
        )
        if sample_gate["blocked_count"]:
            _update_summary_for_stop(summary, status="sample_gate_blocked", stop_condition="sample_gate_blocked")
            write_reports(summary, details, report_json=report_json, report_md=report_md, local_details_json=local_details_json)
            session.rollback()
            return summary
        if args.write_db_records:
            _update_summary_for_stop(
                summary,
                status="provider_policy_blocked",
                stop_condition="db_writes_deferred_until_provider_pilot_validated",
            )
            summary["db_writes"]["attempted"] = False
            summary["db_writes"]["backup_required"] = False
            summary["db_writes"]["restore_recovery_note"] = (
                "DB writes are deferred until the first live provider behavior validation is reviewed."
            )
            write_reports(summary, details, report_json=report_json, report_md=report_md, local_details_json=local_details_json)
            session.rollback()
            return summary
        if not args.execute_live:
            _update_summary_for_stop(summary, status="live_not_requested", stop_condition="live_not_requested")
            write_reports(summary, details, report_json=report_json, report_md=report_md, local_details_json=local_details_json)
            session.rollback()
            return summary
        if not args.upload_derived_approved:
            _update_summary_for_stop(summary, status="provider_policy_blocked", stop_condition="derived_upload_approval_missing")
            write_reports(summary, details, report_json=report_json, report_md=report_md, local_details_json=local_details_json)
            session.rollback()
            return summary
        if not api_key:
            _update_summary_for_stop(summary, status="credential_required", stop_condition="credential_required")
            write_reports(summary, details, report_json=report_json, report_md=report_md, local_details_json=local_details_json)
            session.rollback()
            return summary
        if not args.provider_docs_verified:
            _update_summary_for_stop(summary, status="provider_policy_blocked", stop_condition="provider_docs_not_verified")
            write_reports(summary, details, report_json=report_json, report_md=report_md, local_details_json=local_details_json)
            session.rollback()
            return summary

        public_results, detail_results, result_counts, db_counts = execute_live_requests(
            session,
            media_by_id=media_by_id,
            sample_gate=sample_gate,
            storage_root=config.storage_root,
            derived_dir=derived_dir,
            api_key=api_key,
            write_db_records=bool(args.write_db_records),
            http_post=http_post,
        )
        total_wait_seconds = int(db_counts.pop("__total_wait_seconds", 0))
        _apply_live_execution_state(
            summary,
            details,
            sample_gate=sample_gate,
            public_results=public_results,
            detail_results=detail_results,
            result_counts=result_counts,
            db_counts=db_counts,
            status="completed",
            stop_condition=None,
            total_wait_seconds=total_wait_seconds,
        )
        high_count = int(result_counts.get("high_confidence_match", 0))
        conflict_count = int(result_counts.get("conflict", 0))
        summary["manual_review_burden_estimate"] = {
            "high_confidence_matches": high_count,
            "conflicts": conflict_count,
            "estimated_targeted_review_items": high_count + conflict_count,
        }
        summary["larger_pilot_suitability"] = {
            "suitable_for_larger_pilot": high_count > 0 and int(result_counts.get("provider_error", 0)) == 0 and conflict_count == 0,
            "reason": "based_on_tiny_live_pilot_profile",
            "phase39_required_before_scaling": True,
        }
        quota = summary["quota_observations"]
        quota_blocked = bool(
            quota.get("short_quota_exhausted")
            or quota.get("daily_quota_exhausted")
            or quota.get("out_of_searches")
        )
        summary["subscription_analysis"] = {
            "recommended_now": bool(high_count > 0 and quota_blocked),
            "reason": (
                "consider_subscription_for_quota_only_after_manual_review_confirms_match_quality"
                if high_count > 0 and quota_blocked
                else "not_needed_for_this_5_sample_run; reassess_after_manual_review_and_before_scale"
            ),
            "likely_solves": "quota_or_throughput_only_not_match_quality",
            "purchase_or_subscription_performed": False,
        }
        session.rollback()
        write_reports(summary, details, report_json=report_json, report_md=report_md, local_details_json=local_details_json)
        return summary
    except ProviderStop as exc:
        if summary is None:
            raise
        session.rollback()
        _apply_live_execution_state(
            summary,
            details,
            sample_gate=sample_gate,
            public_results=exc.public_results,
            detail_results=exc.details_results,
            result_counts=exc.result_counts,
            db_counts=exc.db_counts,
            status="partial_run_stopped",
            stop_condition=exc.stop_condition,
            total_wait_seconds=exc.total_wait_seconds,
        )
        write_reports(summary, details, report_json=report_json, report_md=report_md, local_details_json=local_details_json)
        return summary
    except ProviderPolicyBlocked as exc:
        if summary is None:
            raise
        session.rollback()
        _update_summary_for_stop(summary, status="provider_policy_blocked", stop_condition=str(exc))
        write_reports(summary, details, report_json=report_json, report_md=report_md, local_details_json=local_details_json)
        return summary
    finally:
        session.close()
        engine.dispose()


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    try:
        summary = run(args)
    except Phase44B1Error as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"status": summary["status"], "stop_condition": summary.get("stop_condition")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
