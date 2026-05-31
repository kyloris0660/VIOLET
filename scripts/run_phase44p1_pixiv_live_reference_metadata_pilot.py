"""Phase 4.4-P1 Pixiv live reference / metadata / correspondence pilot.

Lifecycle: phase-scoped operational runner. This runner makes a bounded,
non-authenticated public-page probe for a tiny Pixiv filename-prior sample.
It writes no DB rows, performs no login/cookie/browser automation, and keeps
exact Pixiv IDs, URLs, snippets, and local paths only in ignored local artifacts.
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable

from PIL import Image, ImageOps
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models import Media  # noqa: E402
from scripts import run_phase44p0_pixiv_source_prior_auto_verify as p0  # noqa: E402

PHASE = "4.4-P1"
REPORT_MD = Path("docs/reports/phase-4.4p1-pixiv-live-reference-metadata-pilot.md")
REPORT_JSON = Path("docs/reports/phase-4.4p1-pixiv-live-reference-metadata-pilot-summary.json")
LOCAL_REFERENCE_DETAILS_JSON = Path(".local_manifests/phase-4.4p1-pixiv-live-reference-details.json")
LOCAL_CORRESPONDENCE_DETAILS_JSON = Path(".local_manifests/phase-4.4p1-pixiv-correspondence-details.json")
LOCAL_SHEET_MD = Path(".local_manifests/phase-4.4p1-pixiv-metadata-sheet.md")
LOCAL_SHEET_CSV = Path(".local_manifests/phase-4.4p1-pixiv-metadata-sheet.csv")
LOCAL_PREVIEW_DIR = Path(".local_manifests/phase-4.4p1-pixiv-preview-derived")
LOCAL_MANUAL_VALIDATION_SHEET_MD = Path(".local_manifests/phase-4.4p1-pixiv-manual-validation-sheet.md")
LOCAL_MANUAL_VALIDATION_SHEET_CSV = Path(".local_manifests/phase-4.4p1-pixiv-manual-validation-sheet.csv")
LOCAL_MANUAL_VALIDATION_CONTACT_SHEET_HTML = Path(".local_manifests/phase-4.4p1-pixiv-manual-validation-contact-sheet.html")
LOCAL_MANUAL_VALIDATION_CONTACT_SHEET_MD = Path(".local_manifests/phase-4.4p1-pixiv-manual-validation-contact-sheet.md")

PIXIV_ARTWORK_URL = "https://www.pixiv.net/artworks/{work_id}"
DEFAULT_SAMPLE_SIZE = 5
MAX_SAMPLE_SIZE = 10
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_DELAY_SECONDS = 2.0
MAX_PREVIEW_BYTES = 2_500_000
PIXIV_PAGE_HOST_ALLOWLIST = frozenset({"www.pixiv.net", "pixiv.net"})
PIXIV_IMAGE_HOST_ALLOWLIST = frozenset({"embed.pixiv.net", "i.pximg.net", "i-f.pximg.net", "i-cf.pximg.net"})
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
PRE_REVIEW_CORRESPONDENCE_BASELINE = {"auto_verified_high_confidence": 1, "auto_rejected_mismatch": 4}
USER_AGENT = "VIOLET-P1-PixivPublicProbe/1.0 (no cookies; no browser automation)"
WRITE_SQL_RE = p0.WRITE_SQL_RE
LOCAL_PATH_RE = p0.LOCAL_PATH_RE
SECRET_TEXT_RE = p0.SECRET_TEXT_RE
STRONG_BLOCKED_TEXT_RE = re.compile(r"(?i)(captcha|recaptcha|verify you are human|access denied)")
LOGIN_WALL_TEXT_RE = re.compile(r"(?i)(login required|please log in|please login|consent)")
PIXIV_ID_IN_URL_RE = re.compile(r"/artworks/([1-9]\d{5,11})")
PIXIV_ARTWORK_PATH_RE = re.compile(r"^/(?:[a-z]{2}/)?artworks/[1-9]\d{5,11}/?$")


class Phase44P1Error(RuntimeError):
    pass


class EnvBlockedError(Phase44P1Error):
    pass


class IdentityBlockedError(Phase44P1Error):
    pass


class OutputPathError(Phase44P1Error):
    pass


class PrivacyBlocked(Phase44P1Error):
    pass


class ReadOnlyViolation(Phase44P1Error):
    pass


@dataclass(frozen=True)
class ProjectConfig:
    project_root: Path
    violet_env: str
    storage_root: Path
    storage_root_explicitly_set: bool
    settings_file: Path
    settings_source: str
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
class OutputPaths:
    report_md: Path
    report_json: Path
    reference_details_json: Path
    correspondence_details_json: Path
    sheet_md: Path
    sheet_csv: Path
    preview_dir: Path
    manual_validation_sheet_md: Path
    manual_validation_sheet_csv: Path
    manual_validation_contact_sheet_html: Path
    manual_validation_contact_sheet_md: Path


@dataclass(frozen=True)
class HttpResult:
    url: str
    final_url: str
    status: int | None
    content_type: str | None
    content_length_header: int | None
    body: bytes
    error: str | None = None
    redirect_location: str | None = None


@dataclass(frozen=True)
class ImageSignature:
    width: int
    height: int
    aspect_ratio: float
    average_color: tuple[int, int, int]
    ahash: int
    dhash: int


class MetadataHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, str] = {}
        self.links: dict[str, str] = {}
        self.json_ld: list[str] = []
        self.scripts: list[dict[str, str]] = []
        self._capture_json_ld = False
        self._capture_script_id: str | None = None
        self._script_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "meta":
            key = attr.get("property") or attr.get("name") or attr.get("id")
            if key and "content" in attr:
                self.meta[key] = html.unescape(attr["content"])
        elif tag.lower() == "link":
            rel = attr.get("rel")
            href = attr.get("href")
            if rel and href:
                self.links[rel] = html.unescape(href)
        elif tag.lower() == "script":
            script_type = attr.get("type", "").lower()
            script_id = attr.get("id")
            if script_type == "application/ld+json":
                self._capture_json_ld = True
                self._script_chunks = []
            elif script_id:
                self._capture_script_id = script_id
                self._script_chunks = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "script":
            return
        if self._capture_json_ld:
            self.json_ld.append("".join(self._script_chunks))
        elif self._capture_script_id:
            self.scripts.append({"id": self._capture_script_id, "text": "".join(self._script_chunks)})
        self._capture_json_ld = False
        self._capture_script_id = None
        self._script_chunks = []

    def handle_data(self, data: str) -> None:
        if self._capture_json_ld or self._capture_script_id:
            self._script_chunks.append(data)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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


def _load_file_settings(settings_file: Path) -> dict[str, Any]:
    if not settings_file.exists():
        return {}
    try:
        data = json.loads(settings_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IdentityBlockedError("identity_blocked: settings.json is not valid JSON") from exc
    return data if isinstance(data, dict) else {}


def load_project_config(project_root: Path = ROOT) -> ProjectConfig:
    project_root = project_root.resolve()
    dotenv_values = _read_dotenv(project_root / ".env")
    violet_env_raw = _env_value(dotenv_values, "VIOLET_ENV", "").strip()
    violet_env = (violet_env_raw or "development").lower()
    if violet_env != "development":
        reported_env = violet_env_raw or "unset"
        raise EnvBlockedError(f"env_blocked: VIOLET_ENV must be 'development' for {PHASE}; got {reported_env!r}")
    if _env_value(dotenv_values, "TEST_DATABASE_URL", "").strip():
        raise EnvBlockedError("env_blocked: TEST_DATABASE_URL is set; refusing development DB audit")

    storage_env = _env_value(dotenv_values, "VIOLET_STORAGE_ROOT", "").strip()
    storage_root = (Path(storage_env) if storage_env else project_root).resolve()
    storage_root_explicit = bool(storage_env)
    settings_file = storage_root / "data" / "settings.json"
    if storage_root_explicit and not settings_file.exists():
        raise IdentityBlockedError("identity_blocked: explicit VIOLET_STORAGE_ROOT has no data/settings.json")
    file_settings = _load_file_settings(settings_file)
    settings_source = (
        "storage_root_data_settings_json"
        if storage_root_explicit
        else ("code_root_data_settings_json" if settings_file.exists() else "dotenv_or_defaults")
    )
    db_settings = file_settings.get("database", {}) if isinstance(file_settings.get("database"), dict) else {}
    db_user = str(db_settings.get("user") or "").strip() or _env_value(dotenv_values, "POSTGRES_USER", "").strip() or "postgres"
    db_password = str(db_settings.get("password") or "") or _env_value(dotenv_values, "POSTGRES_PASSWORD", "")
    db_host = str(db_settings.get("host") or "").strip() or _env_value(dotenv_values, "POSTGRES_HOST", "").strip() or "localhost"
    db_port = int(str(db_settings.get("port") or "").strip() or _env_value(dotenv_values, "POSTGRES_PORT", "").strip() or "5432")
    db_name = str(db_settings.get("name") or "").strip() or _env_value(dotenv_values, "POSTGRES_DB", "").strip() or "blombooru"
    if db_name == "blombooru_test":
        raise IdentityBlockedError("identity_blocked: target DB is blombooru_test, not blombooru")
    return ProjectConfig(
        project_root=project_root,
        violet_env=violet_env,
        storage_root=storage_root,
        storage_root_explicitly_set=storage_root_explicit,
        settings_file=settings_file.resolve(),
        settings_source=settings_source,
        db_user=db_user,
        db_password=db_password,
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
    )


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
        "code_root_label": "repo_root",
        "storage_root_mode": "explicit_violet_storage_root" if config.storage_root_explicitly_set else "code_root_default",
        "settings_source": config.settings_source,
        "configured_db_host": config.db_host,
        "configured_db_port": config.db_port,
        "configured_db_user": config.db_user,
        "configured_db_name": config.db_name,
        "actual_db_name": str(actual_db),
        "db_identity_result": "development_blombooru_confirmed",
        "db_password_included": False,
        "local_paths_redacted": True,
    }


def resolve_output_path(path_text: str | Path, *, expected_parent: Path) -> Path:
    path = Path(path_text)
    resolved = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    expected = (ROOT / expected_parent).resolve()
    try:
        resolved.relative_to(expected)
    except ValueError as exc:
        raise OutputPathError(f"output_path_outside_expected_parent: {resolved}") from exc
    return resolved


def resolve_output_paths(args: argparse.Namespace) -> OutputPaths:
    return OutputPaths(
        report_md=resolve_output_path(args.report_md, expected_parent=Path("docs/reports")),
        report_json=resolve_output_path(args.report_json, expected_parent=Path("docs/reports")),
        reference_details_json=resolve_output_path(args.reference_details_json, expected_parent=Path(".local_manifests")),
        correspondence_details_json=resolve_output_path(args.correspondence_details_json, expected_parent=Path(".local_manifests")),
        sheet_md=resolve_output_path(args.sheet_md, expected_parent=Path(".local_manifests")),
        sheet_csv=resolve_output_path(args.sheet_csv, expected_parent=Path(".local_manifests")),
        preview_dir=resolve_output_path(args.preview_dir, expected_parent=Path(".local_manifests")),
        manual_validation_sheet_md=resolve_output_path(args.manual_validation_sheet_md, expected_parent=Path(".local_manifests")),
        manual_validation_sheet_csv=resolve_output_path(args.manual_validation_sheet_csv, expected_parent=Path(".local_manifests")),
        manual_validation_contact_sheet_html=resolve_output_path(args.manual_validation_contact_sheet_html, expected_parent=Path(".local_manifests")),
        manual_validation_contact_sheet_md=resolve_output_path(args.manual_validation_contact_sheet_md, expected_parent=Path(".local_manifests")),
    )


def public_path_label(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return f"redacted_external_output:{resolved.name}"


def build_public_artifact_labels(paths: OutputPaths) -> dict[str, Any]:
    return {
        "report_md": public_path_label(paths.report_md),
        "report_json": public_path_label(paths.report_json),
        "reference_details_json": public_path_label(paths.reference_details_json),
        "correspondence_details_json": public_path_label(paths.correspondence_details_json),
        "metadata_sheet_md": public_path_label(paths.sheet_md),
        "metadata_sheet_csv": public_path_label(paths.sheet_csv),
        "preview_dir": public_path_label(paths.preview_dir),
        "manual_validation_sheet_md": public_path_label(paths.manual_validation_sheet_md),
        "manual_validation_sheet_csv": public_path_label(paths.manual_validation_sheet_csv),
        "manual_validation_contact_sheet_html": public_path_label(paths.manual_validation_contact_sheet_html),
        "manual_validation_contact_sheet_md": public_path_label(paths.manual_validation_contact_sheet_md),
        "full_local_paths_public": False,
        "artifacts_are_gitignored": True,
    }


def local_artifact_path_details(paths: OutputPaths) -> dict[str, str]:
    return {
        "report_md": str(paths.report_md),
        "report_json": str(paths.report_json),
        "reference_details_json": str(paths.reference_details_json),
        "correspondence_details_json": str(paths.correspondence_details_json),
        "metadata_sheet_md": str(paths.sheet_md),
        "metadata_sheet_csv": str(paths.sheet_csv),
        "preview_dir": str(paths.preview_dir),
        "manual_validation_sheet_md": str(paths.manual_validation_sheet_md),
        "manual_validation_sheet_csv": str(paths.manual_validation_sheet_csv),
        "manual_validation_contact_sheet_html": str(paths.manual_validation_contact_sheet_html),
        "manual_validation_contact_sheet_md": str(paths.manual_validation_contact_sheet_md),
    }


def _detail_categories(detail: dict[str, Any], duplicate_work_ids: set[str]) -> list[str]:
    categories: set[str] = set()
    if detail.get("content_class") == "anime":
        categories.add("content_class_anime")
    for match in detail.get("matches", []):
        categories.update(match.get("contexts", []))
        if match.get("pixiv_work_id") in duplicate_work_ids:
            categories.add("duplicate_work_id_case")
    return sorted(categories)


def select_p1_sample(private_details: dict[str, Any], media_by_id: dict[int, Media], *, sample_size: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if sample_size < 1 or sample_size > MAX_SAMPLE_SIZE:
        raise ValueError(f"sample_size must be 1..{MAX_SAMPLE_SIZE}")
    details = [item for item in private_details.get("details", []) if item.get("matches")]
    work_counts: Counter[str] = Counter()
    for detail in details:
        for work_id in {match["pixiv_work_id"] for match in detail.get("matches", [])}:
            work_counts[work_id] += 1
    duplicate_work_ids = {work_id for work_id, count in work_counts.items() if count > 1}
    enriched = [
        {
            **detail,
            "selection_categories": _detail_categories(detail, duplicate_work_ids),
        }
        for detail in details
        if int(detail["media_id"]) in media_by_id
    ]
    anime_enriched = [detail for detail in enriched if detail.get("content_class") == "anime"]
    required_categories = [
        "non_p0_page",
        "suffix_timestamp_case",
        "prefixed_token",
        "duplicate_work_id_case",
        "p0_page",
    ]
    selected: list[dict[str, Any]] = []
    selected_ids: set[int] = set()

    def add_if_new(detail: dict[str, Any]) -> None:
        media_id = int(detail["media_id"])
        if media_id not in selected_ids and len(selected) < sample_size:
            media = media_by_id[media_id]
            selected.append(
                {
                    **detail,
                    "local_media": {
                        "filename": media.filename,
                        "filename_basename": p0._basename_from_metadata(media.filename),
                        "thumbnail_path": media.thumbnail_path,
                        "path": media.path,
                        "file_type": p0._enum_label(media.file_type) or "unknown",
                        "mime_type": media.mime_type,
                    },
                }
            )
            selected_ids.add(media_id)

    for category in required_categories:
        for detail in anime_enriched:
            if category in detail["selection_categories"]:
                add_if_new(detail)
                break
    for detail in sorted(anime_enriched, key=lambda item: int(item["media_id"])):
        add_if_new(detail)
        if len(selected) >= sample_size:
            break

    category_counts: Counter[str] = Counter()
    content_counts: Counter[str] = Counter()
    page_case_counts: Counter[str] = Counter()
    for detail in selected:
        content_counts[detail.get("content_class") or "unset"] += 1
        category_counts.update(detail.get("selection_categories", []))
        first_match = first_canonical_match(detail)
        page_case_counts["non_p0" if int(first_match["page_index"]) > 0 else "p0"] += 1
    public = {
        "sample_scope": "real_extracted_pixiv_prior_candidates",
        "selected_count": len(selected),
        "requested_sample_size": sample_size,
        "max_items": MAX_SAMPLE_SIZE,
        "anime_only": True,
        "available_anime_candidate_count": len(anime_enriched),
        "non_anime_candidates_excluded": max(0, len(enriched) - len(anime_enriched)),
        "insufficient_anime_candidates": len(selected) < sample_size,
        "selection_strategy": "cover_non_p0_suffix_prefix_duplicate_work_id_and_p0_then_fill_anime_first",
        "category_counts": dict(sorted(category_counts.items())),
        "content_class_distribution": dict(sorted(content_counts.items())),
        "page_case_distribution": dict(sorted(page_case_counts.items())),
        "exact_media_ids_public": False,
        "exact_pixiv_ids_public": False,
    }
    return public, selected


def first_canonical_match(detail: dict[str, Any]) -> dict[str, Any]:
    matches = detail.get("matches", [])
    for preferred in ("stored_filename", "stored_path_basename", "app_managed_thumbnail_basename"):
        for match in matches:
            if match.get("source_field") == preferred:
                return match
    if not matches:
        raise ValueError("sample detail has no pixiv match")
    return matches[0]


def build_network_policy(sample_size: int, delay_seconds: float, timeout_seconds: float) -> dict[str, Any]:
    return {
        "sample_size": sample_size,
        "max_sample_size": MAX_SAMPLE_SIZE,
        "concurrency": 1,
        "timeout_seconds": timeout_seconds,
        "delay_seconds": delay_seconds,
        "cookies": False,
        "browser_session": False,
        "login": False,
        "browser_automation": False,
        "retry_storm": False,
        "stop_on_403_429_login_captcha_consent_or_antibot": True,
        "referer_header": False,
        "full_original_download": False,
        "public_report_exact_urls": False,
    }


def build_safe_headers(*, accept: str) -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.8",
        "Cache-Control": "no-cache",
    }


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def safe_http_get(url: str, *, accept: str, timeout_seconds: float, max_bytes: int, allow_redirects: bool = True) -> HttpResult:
    request = urllib.request.Request(url, headers=build_safe_headers(accept=accept), method="GET")
    try:
        opener = urllib.request.build_opener(_NoRedirectHandler) if not allow_redirects else None
        response_cm = opener.open(request, timeout=timeout_seconds) if opener else urllib.request.urlopen(request, timeout=timeout_seconds)
        with response_cm as response:
            body = response.read(max_bytes + 1)
            headers = response.headers
            return HttpResult(
                url=url,
                final_url=response.geturl(),
                status=response.status,
                content_type=headers.get("Content-Type"),
                content_length_header=_safe_int(headers.get("Content-Length")),
                body=body[:max_bytes],
                error="response_truncated_at_max_bytes" if len(body) > max_bytes else None,
            )
    except urllib.error.HTTPError as exc:
        body = exc.read(min(max_bytes, 65536))
        redirect_location = exc.headers.get("Location")
        return HttpResult(
            url=url,
            final_url=urllib.parse.urljoin(url, redirect_location) if redirect_location else exc.geturl(),
            status=exc.code,
            content_type=exc.headers.get("Content-Type"),
            content_length_header=_safe_int(exc.headers.get("Content-Length")),
            body=body,
            error=f"http_error_{exc.code}",
            redirect_location=redirect_location,
        )
    except urllib.error.URLError as exc:
        return HttpResult(
            url=url,
            final_url=url,
            status=None,
            content_type=None,
            content_length_header=None,
            body=b"",
            error=f"url_error:{type(exc.reason).__name__}",
        )


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def decode_body(result: HttpResult) -> str:
    content_type = result.content_type or ""
    charset = "utf-8"
    match = re.search(r"charset=([A-Za-z0-9._-]+)", content_type)
    if match:
        charset = match.group(1)
    return result.body.decode(charset, errors="replace")


def detect_blocked_page(status: int | None, text_body: str, *, content_type: str | None = None) -> tuple[bool, str | None]:
    if status in {403, 429}:
        return True, f"http_{status}"
    if status is None:
        return True, "network_error"
    probe_text = text_body[:200_000]
    has_public_metadata = (
        'property="og:title"' in probe_text
        or "property='og:title'" in probe_text
        or "meta-preload-data" in probe_text
        or "application/ld+json" in probe_text
    )
    if STRONG_BLOCKED_TEXT_RE.search(probe_text) and not has_public_metadata:
        return True, "login_captcha_consent_or_antibot_marker"
    if LOGIN_WALL_TEXT_RE.search(probe_text) and not has_public_metadata:
        return True, "login_captcha_consent_or_antibot_marker"
    if content_type and "text/html" in content_type.lower() and "pixiv" in probe_text.lower():
        if "signup" in probe_text.lower() and "login" in probe_text.lower() and not has_public_metadata:
            return True, "login_wall_possible"
    return False, None


def parse_public_metadata(text_body: str) -> dict[str, Any]:
    parser = MetadataHTMLParser()
    parser.feed(text_body)
    fields: dict[str, Any] = {
        "title": parser.meta.get("og:title") or parser.meta.get("twitter:title"),
        "description": parser.meta.get("og:description") or parser.meta.get("description") or parser.meta.get("twitter:description"),
        "canonical_url": parser.links.get("canonical") or parser.meta.get("og:url"),
        "preview_image_candidates": [],
        "json_ld_types": [],
        "preload_payload_found": False,
        "preload_data_found": False,
        "preload_parse_error_count": 0,
        "page_count": None,
        "tags": [],
        "artist_user_name": None,
        "artist_user_id": None,
        "metadata_fields_found": [],
        "page_index_discoverable": False,
    }
    for key in ("og:image", "twitter:image", "twitter:image:src"):
        if parser.meta.get(key):
            fields["preview_image_candidates"].append(parser.meta[key])
    for raw_json in parser.json_ld:
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@type"):
                fields["json_ld_types"].append(item.get("@type"))
            fields["title"] = fields["title"] or item.get("name") or item.get("headline")
            fields["description"] = fields["description"] or item.get("description")
            image_value = item.get("image")
            if isinstance(image_value, str):
                fields["preview_image_candidates"].append(image_value)
            elif isinstance(image_value, list):
                fields["preview_image_candidates"].extend([value for value in image_value if isinstance(value, str)])
    for script in parser.scripts:
        if script.get("id") != "meta-preload-data":
            continue
        _parse_and_merge_preload_payload(fields, script.get("text") or "{}")
    for key, value in parser.meta.items():
        lowered_key = key.lower()
        if "meta-preload-data" in lowered_key or "preload-data" in lowered_key:
            _parse_and_merge_preload_payload(fields, value)
    fields["preview_image_candidates"] = sorted(set(fields["preview_image_candidates"]))
    fields["metadata_fields_found"] = sorted(
        key
        for key in (
            "title",
            "description",
            "canonical_url",
            "preview_image_candidates",
            "json_ld_types",
            "preload_payload_found",
            "preload_data_found",
            "page_count",
            "tags",
            "artist_user_name",
            "artist_user_id",
        )
        if fields.get(key)
    )
    return fields


def _parse_and_merge_preload_payload(fields: dict[str, Any], payload: str) -> None:
    fields["preload_payload_found"] = True
    try:
        data = json.loads(html.unescape(payload or "{}"))
    except json.JSONDecodeError:
        fields["preload_parse_error_count"] = int(fields.get("preload_parse_error_count") or 0) + 1
        return
    if not isinstance(data, dict):
        return
    fields["preload_data_found"] = True
    _merge_pixiv_preload_data(fields, data)


def _merge_pixiv_preload_data(fields: dict[str, Any], data: dict[str, Any]) -> None:
    illust = data.get("illust") if isinstance(data.get("illust"), dict) else {}
    if not illust:
        return
    first_item = next((item for item in illust.values() if isinstance(item, dict)), None)
    if not first_item:
        return
    fields["title"] = fields["title"] or first_item.get("title")
    fields["description"] = fields["description"] or first_item.get("description")
    fields["artist_user_name"] = first_item.get("userName") or first_item.get("userAccount") or fields["artist_user_name"]
    fields["artist_user_id"] = first_item.get("userId") or fields["artist_user_id"]
    page_count = first_item.get("pageCount")
    if isinstance(page_count, int):
        fields["page_count"] = page_count
        fields["page_index_discoverable"] = True
    tags = first_item.get("tags")
    if isinstance(tags, dict) and isinstance(tags.get("tags"), list):
        parsed_tags = []
        for item in tags["tags"]:
            if isinstance(item, dict):
                tag_name = item.get("tag")
                if tag_name:
                    parsed_tags.append(str(tag_name))
        fields["tags"] = parsed_tags
    urls = first_item.get("urls")
    if isinstance(urls, dict):
        for key in ("mini", "thumb", "small", "regular"):
            value = urls.get(key)
            if isinstance(value, str):
                fields["preview_image_candidates"].append(value)


def classify_metadata_richness(metadata: dict[str, Any], *, blocked: bool) -> str:
    if blocked:
        return "blocked"
    has_title = bool(metadata.get("title"))
    has_artist = bool(metadata.get("artist_user_name") or metadata.get("artist_user_id"))
    has_tags = bool(metadata.get("tags"))
    has_preview = bool(metadata.get("preview_image_candidates"))
    if has_title and has_artist and has_tags:
        return "rich_structured_metadata"
    if has_title and has_artist:
        return "partial_metadata_title_artist"
    if has_preview:
        return "preview_only"
    return "metadata_limited_requires_followup"


def is_original_or_disallowed_preview(url: str) -> bool:
    lowered = url.lower()
    return any(marker in lowered for marker in ("/img-original/", "_ugoira", ".zip"))


def url_host(url: str | None) -> str | None:
    if not url:
        return None
    return urllib.parse.urlparse(url).hostname


def url_scheme(url: str | None) -> str:
    if not url:
        return ""
    return urllib.parse.urlparse(url).scheme.lower()


def is_https_url(url: str | None) -> bool:
    return url_scheme(url) == "https"


def is_allowed_pixiv_image_url(url: str | None) -> bool:
    host = url_host(url)
    return bool(is_https_url(url) and host and host.lower() in PIXIV_IMAGE_HOST_ALLOWLIST)


def is_allowed_pixiv_image_host(url: str | None) -> bool:
    return is_allowed_pixiv_image_url(url)


def is_allowed_pixiv_page_url(url: str | None) -> bool:
    if not is_https_url(url):
        return False
    parsed = urllib.parse.urlparse(url or "")
    host = parsed.hostname
    return bool(host and host.lower() in PIXIV_PAGE_HOST_ALLOWLIST and PIXIV_ARTWORK_PATH_RE.match(parsed.path))


def blocked_page_redirect_reason(url: str | None) -> str:
    parsed = urllib.parse.urlparse(url or "")
    host = parsed.hostname or ""
    path_query = f"{parsed.path}?{parsed.query}".lower()
    if any(marker in path_query for marker in ("login", "captcha", "consent", "signup", "verification", "verify")):
        return "redirect_blocked_login_captcha_consent_or_antibot"
    if parsed.scheme.lower() != "https":
        return "redirect_blocked_non_https"
    if host.lower() not in PIXIV_PAGE_HOST_ALLOWLIST:
        return "redirect_blocked_unexpected_host"
    return "redirect_blocked_non_artwork_page"


def preview_url_policy_rejection_reason(url: str | None, *, stage: str) -> str | None:
    if not url:
        return f"{stage}_url_missing"
    if not is_https_url(url):
        return f"{stage}_url_non_https"
    host = url_host(url)
    if not host or host.lower() not in PIXIV_IMAGE_HOST_ALLOWLIST:
        return f"{stage}_url_unexpected_host"
    return None


def _blocked_preview_result(
    *,
    reason: str,
    preview_url: str | None,
    final_url: str | None = None,
    http_status: int | None = None,
) -> dict[str, Any]:
    policy_status = "blocked_non_https" if "non_https" in reason else "blocked_unexpected_host"
    return {
        "status": "preview_fetch_blocked_unexpected_host",
        "image_path": None,
        "http_status": http_status,
        "reason": reason,
        "host_policy_status": policy_status,
        "preview_url_host": url_host(preview_url),
        "preview_url_scheme": url_scheme(preview_url),
        "final_url_host": url_host(final_url),
        "final_url_scheme": url_scheme(final_url),
    }


def safe_filename(prefix: str, index: int, suffix: str) -> str:
    return f"{prefix}_{index:02d}.{suffix.lstrip('.')}"


def attach_preview_candidate_stats(result: dict[str, Any], stats: dict[str, int]) -> dict[str, Any]:
    return {**result, **stats}


def fetch_preview_image(
    preview_url: str | None,
    *,
    output_dir: Path,
    index: int,
    timeout_seconds: float,
    http_get: Callable[..., HttpResult] = safe_http_get,
) -> dict[str, Any]:
    if not preview_url:
        return {
            "status": "reference_unavailable",
            "image_path": None,
            "reason": "no_preview_url_candidate",
            "host_policy_status": "no_preview_url_candidate",
        }
    rejection_reason = preview_url_policy_rejection_reason(preview_url, stage="initial")
    if rejection_reason:
        return _blocked_preview_result(reason=rejection_reason, preview_url=preview_url)
    if is_original_or_disallowed_preview(preview_url):
        return {
            "status": "preview_fetch_blocked",
            "image_path": None,
            "reason": "original_or_disallowed_preview_url",
            "host_policy_status": "blocked_original_or_disallowed",
            "preview_url_host": url_host(preview_url),
        }
    current_url = preview_url
    result: HttpResult | None = None
    for _redirect_count in range(4):
        result = http_get(
            current_url,
            accept="image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            timeout_seconds=timeout_seconds,
            max_bytes=MAX_PREVIEW_BYTES,
            allow_redirects=False,
        )
        if result.status not in REDIRECT_STATUSES:
            break
        redirected_url = result.redirect_location or result.final_url
        if not redirected_url:
            return {
                "status": "reference_unavailable",
                "image_path": None,
                "http_status": result.status,
                "reason": "redirect_without_location",
                "host_policy_status": "allowed_pixiv_image_host",
                "preview_url_host": url_host(preview_url),
                "final_url_host": url_host(result.final_url),
            }
        redirected_url = urllib.parse.urljoin(current_url, redirected_url)
        rejection_reason = preview_url_policy_rejection_reason(redirected_url, stage="redirect")
        if rejection_reason:
            return _blocked_preview_result(
                reason=rejection_reason,
                preview_url=preview_url,
                final_url=redirected_url,
                http_status=result.status,
            )
        if is_original_or_disallowed_preview(redirected_url):
            return {
                "status": "preview_fetch_blocked",
                "image_path": None,
                "http_status": result.status,
                "reason": "redirect_to_original_or_disallowed_preview_url",
                "host_policy_status": "blocked_original_or_disallowed",
                "preview_url_host": url_host(preview_url),
                "final_url_host": url_host(redirected_url),
            }
        current_url = redirected_url
    else:
        return {
            "status": "reference_unavailable",
            "image_path": None,
            "reason": "too_many_preview_redirects",
            "host_policy_status": "allowed_pixiv_image_host",
            "preview_url_host": url_host(preview_url),
            "final_url_host": url_host(current_url),
        }
    if result is None:
        return {"status": "reference_unavailable", "image_path": None, "reason": "preview_fetch_not_attempted"}
    rejection_reason = preview_url_policy_rejection_reason(result.final_url, stage="final")
    if rejection_reason:
        return _blocked_preview_result(
            reason=rejection_reason,
            preview_url=preview_url,
            final_url=result.final_url,
            http_status=result.status,
        )
    if result.status in {403, 429}:
        return {
            "status": "preview_fetch_blocked",
            "image_path": None,
            "http_status": result.status,
            "reason": f"http_{result.status}",
            "host_policy_status": "allowed_pixiv_image_host",
            "preview_url_host": url_host(preview_url),
            "final_url_host": url_host(result.final_url),
        }
    if result.status != 200 or not result.body:
        return {
            "status": "reference_unavailable",
            "image_path": None,
            "http_status": result.status,
            "reason": result.error or "non_200_or_empty",
            "host_policy_status": "allowed_pixiv_image_host",
            "preview_url_host": url_host(preview_url),
            "final_url_host": url_host(result.final_url),
        }
    content_type = result.content_type or ""
    if "image/" not in content_type.lower():
        return {
            "status": "reference_unavailable",
            "image_path": None,
            "http_status": result.status,
            "reason": "non_image_content_type",
            "host_policy_status": "allowed_pixiv_image_host",
            "preview_url_host": url_host(preview_url),
            "final_url_host": url_host(result.final_url),
        }
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / safe_filename("pixiv_preview_stripped", index, "jpg")
    try:
        with Image.open(io.BytesIO(result.body)) as image:
            normalized = ImageOps.exif_transpose(image).convert("RGB")
            normalized.save(target, format="JPEG", quality=90)
    except Exception as exc:
        return {
            "status": "reference_unavailable",
            "image_path": None,
            "http_status": result.status,
            "reason": f"image_decode_failed:{type(exc).__name__}",
            "host_policy_status": "allowed_pixiv_image_host",
            "preview_url_host": url_host(preview_url),
            "final_url_host": url_host(result.final_url),
        }
    return {
        "status": "reference_preview_fetched",
        "image_path": str(target),
        "http_status": result.status,
        "content_type": content_type,
        "size_bytes": target.stat().st_size,
        "metadata_stripped": True,
        "host_policy_status": "allowed_pixiv_image_host",
        "preview_url_host": url_host(preview_url),
        "preview_url_scheme": url_scheme(preview_url),
        "final_url_host": url_host(result.final_url),
        "final_url_scheme": url_scheme(result.final_url),
    }


def fetch_preview_from_candidates(
    preview_urls: Iterable[str],
    *,
    output_dir: Path,
    index: int,
    timeout_seconds: float,
    http_get: Callable[..., HttpResult] = safe_http_get,
) -> dict[str, Any]:
    candidates = [url for url in preview_urls if isinstance(url, str) and url]
    stats = {
        "preview_candidates_total": len(candidates),
        "preview_candidates_skipped_unexpected_host": 0,
        "preview_candidates_attempted_allowed": 0,
    }
    last_result: dict[str, Any] | None = None
    for preview_url in candidates:
        if preview_url_policy_rejection_reason(preview_url, stage="initial"):
            stats["preview_candidates_skipped_unexpected_host"] += 1
            last_result = _blocked_preview_result(reason=preview_url_policy_rejection_reason(preview_url, stage="initial") or "initial_url_rejected", preview_url=preview_url)
            continue
        if is_original_or_disallowed_preview(preview_url):
            stats["preview_candidates_skipped_unexpected_host"] += 1
            last_result = {
                "status": "preview_fetch_blocked",
                "image_path": None,
                "reason": "original_or_disallowed_preview_url",
                "host_policy_status": "blocked_original_or_disallowed",
                "preview_url_host": url_host(preview_url),
                "preview_url_scheme": url_scheme(preview_url),
            }
            continue
        stats["preview_candidates_attempted_allowed"] += 1
        result = fetch_preview_image(
            preview_url,
            output_dir=output_dir,
            index=index,
            timeout_seconds=timeout_seconds,
            http_get=http_get,
        )
        last_result = result
        if result.get("status") == "reference_preview_fetched":
            return attach_preview_candidate_stats(result, stats)
    if last_result is None:
        last_result = {
            "status": "reference_unavailable",
            "image_path": None,
            "reason": "no_preview_url_candidate",
            "host_policy_status": "no_preview_url_candidate",
        }
    return attach_preview_candidate_stats(last_result, stats)


def _average_hash(image: Image.Image, *, hash_size: int = 8) -> int:
    gray = ImageOps.grayscale(image).resize((hash_size, hash_size), Image.Resampling.LANCZOS)
    pixels = list(gray.getdata())
    average = sum(pixels) / len(pixels)
    value = 0
    for index, pixel in enumerate(pixels):
        if pixel >= average:
            value |= 1 << index
    return value


def _difference_hash(image: Image.Image, *, hash_size: int = 8) -> int:
    gray = ImageOps.grayscale(image).resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    pixels = list(gray.getdata())
    value = 0
    bit = 0
    for row in range(hash_size):
        offset = row * (hash_size + 1)
        for col in range(hash_size):
            if pixels[offset + col] > pixels[offset + col + 1]:
                value |= 1 << bit
            bit += 1
    return value


def hamming_distance(left: int, right: int) -> int:
    return int((left ^ right).bit_count())


def build_image_signature_from_image(image: Image.Image) -> ImageSignature:
    normalized = ImageOps.exif_transpose(image).convert("RGB")
    width, height = normalized.size
    if width <= 0 or height <= 0:
        raise ValueError("image_dimensions_invalid")
    average_color = tuple(int(channel) for channel in normalized.resize((1, 1)).getpixel((0, 0)))
    return ImageSignature(
        width=width,
        height=height,
        aspect_ratio=width / height,
        average_color=average_color,
        ahash=_average_hash(normalized),
        dhash=_difference_hash(normalized),
    )


def build_image_signature_from_path(path: Path) -> ImageSignature:
    with Image.open(path) as image:
        return build_image_signature_from_image(image)


def compare_image_signatures(local: ImageSignature, reference: ImageSignature) -> dict[str, Any]:
    aspect_delta = abs(local.aspect_ratio - reference.aspect_ratio) / max(local.aspect_ratio, reference.aspect_ratio)
    ahash_distance = hamming_distance(local.ahash, reference.ahash)
    dhash_distance = hamming_distance(local.dhash, reference.dhash)
    color_distance = math.sqrt(
        sum((left - right) ** 2 for left, right in zip(local.average_color, reference.average_color, strict=True))
    )
    if aspect_delta <= 0.03 and ahash_distance <= 8 and dhash_distance <= 10 and color_distance <= 45:
        status = "auto_verified_high_confidence"
    elif aspect_delta >= 0.18 or ahash_distance >= 26 or dhash_distance >= 26 or color_distance >= 130:
        status = "auto_rejected_mismatch"
    else:
        status = "uncertain_needs_manual_or_lookup"
    return {
        "auto_verification_status": status,
        "aspect_ratio_delta": round(aspect_delta, 4),
        "ahash_distance": ahash_distance,
        "dhash_distance": dhash_distance,
        "average_color_distance": round(color_distance, 2),
        "threshold_policy_version": "phase44p1-pilot-v1-not-production",
    }


def resolve_media_image_path(media_info: dict[str, Any], storage_root: Path) -> Path | None:
    for key in ("thumbnail_path", "path"):
        stored = media_info.get(key)
        if not stored:
            continue
        normalized = str(stored).replace("\\", "/")
        if normalized.startswith("/") or ".." in Path(normalized).parts or re.match(r"^[A-Za-z]:", normalized):
            continue
        candidate = (storage_root / normalized).resolve()
        try:
            candidate.relative_to(storage_root.resolve())
        except ValueError:
            continue
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def verify_correspondence(sample: dict[str, Any], preview_result: dict[str, Any], *, storage_root: Path) -> dict[str, Any]:
    preview_path = preview_result.get("image_path")
    if preview_result.get("status") in {"preview_fetch_blocked", "preview_fetch_blocked_unexpected_host"}:
        return {"status": "preview_fetch_blocked", "scores": None, "reason": preview_result.get("reason")}
    if not preview_path:
        return {"status": "metadata_only_no_reference", "scores": None, "reason": preview_result.get("reason")}
    local_path = resolve_media_image_path(sample.get("local_media", {}), storage_root)
    if not local_path:
        return {"status": "unsupported_media_type", "scores": None, "reason": "no_readable_app_managed_image_or_thumbnail"}
    try:
        local_sig = build_image_signature_from_path(local_path)
        reference_sig = build_image_signature_from_path(Path(preview_path))
    except Exception as exc:
        return {"status": "unsupported_media_type", "scores": None, "reason": f"image_signature_failed:{type(exc).__name__}"}
    scores = compare_image_signatures(local_sig, reference_sig)
    return {"status": scores["auto_verification_status"], "scores": scores, "local_input_kind": "app_managed_thumbnail_or_image"}


def fetch_pixiv_page_with_redirect_policy(
    artwork_url: str,
    *,
    timeout_seconds: float,
    http_get: Callable[..., HttpResult] = safe_http_get,
) -> tuple[HttpResult, dict[str, Any]]:
    current_url = artwork_url
    attempts = 0
    redirect_count = 0
    redirect_chain: list[str] = []
    last_result: HttpResult | None = None
    for _ in range(4):
        attempts += 1
        result = http_get(
            current_url,
            accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            timeout_seconds=timeout_seconds,
            max_bytes=1_000_000,
            allow_redirects=False,
        )
        last_result = result
        if result.status not in REDIRECT_STATUSES:
            return result, {
                "page_redirect_policy_status": "no_redirect" if redirect_count == 0 else "followed_safe_redirect",
                "page_redirect_count": redirect_count,
                "page_redirect_chain_hosts": [url_host(url) for url in redirect_chain],
                "network_attempt_count": attempts,
            }
        redirected_url = urllib.parse.urljoin(current_url, result.redirect_location or result.final_url)
        redirect_chain.append(redirected_url)
        if not is_allowed_pixiv_page_url(redirected_url):
            return result, {
                "page_redirect_policy_status": "blocked",
                "page_redirect_blocked_reason": blocked_page_redirect_reason(redirected_url),
                "page_redirect_count": redirect_count + 1,
                "page_redirect_chain_hosts": [url_host(url) for url in redirect_chain],
                "network_attempt_count": attempts,
                "blocked_redirect_url_host": url_host(redirected_url),
                "blocked_redirect_url_scheme": url_scheme(redirected_url),
            }
        current_url = redirected_url
        redirect_count += 1
    if last_result is None:
        last_result = HttpResult(
            url=artwork_url,
            final_url=artwork_url,
            status=None,
            content_type=None,
            content_length_header=None,
            body=b"",
            error="page_fetch_not_attempted",
        )
    return last_result, {
        "page_redirect_policy_status": "blocked",
        "page_redirect_blocked_reason": "too_many_page_redirects",
        "page_redirect_count": redirect_count,
        "page_redirect_chain_hosts": [url_host(url) for url in redirect_chain],
        "network_attempt_count": attempts,
    }


def should_stop_after_blocked_reason(reason: str | None) -> bool:
    if not reason:
        return False
    if reason == "network_error":
        return False
    return (
        reason.startswith("http_403")
        or reason.startswith("http_429")
        or "login" in reason
        or "captcha" in reason
        or "consent" in reason
        or "antibot" in reason
        or reason.startswith("redirect_blocked")
        or reason == "too_many_page_redirects"
    )


def probe_pixiv_pages(
    selected: list[dict[str, Any]],
    *,
    timeout_seconds: float,
    delay_seconds: float,
    preview_dir: Path,
    storage_root: Path,
    page_http_get: Callable[..., HttpResult] = safe_http_get,
    preview_http_get: Callable[..., HttpResult] = safe_http_get,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    page_results: list[dict[str, Any]] = []
    correspondence_results: list[dict[str, Any]] = []
    stop_reason: str | None = None
    for index, sample in enumerate(selected, start=1):
        match = first_canonical_match(sample)
        work_id = str(match["pixiv_work_id"])
        artwork_url = PIXIV_ARTWORK_URL.format(work_id=work_id)
        if stop_reason:
            page_results.append(
                {
                    "media_id": sample["media_id"],
                    "pixiv_work_id": work_id,
                    "page_index": int(match["page_index"]),
                    "url": artwork_url,
                    "status": "not_attempted_after_stop",
                    "network_attempted": False,
                    "stop_reason": stop_reason,
                }
            )
            correspondence_results.append(
                {"media_id": sample["media_id"], "pixiv_work_id": work_id, "status": "pixiv_page_blocked", "reason": stop_reason}
            )
            continue
        result, page_fetch_policy = fetch_pixiv_page_with_redirect_policy(
            artwork_url,
            timeout_seconds=timeout_seconds,
            http_get=page_http_get,
        )
        text_body = decode_body(result)
        blocked, blocked_reason = detect_blocked_page(result.status, text_body, content_type=result.content_type)
        if page_fetch_policy.get("page_redirect_policy_status") == "blocked":
            blocked = True
            blocked_reason = str(page_fetch_policy.get("page_redirect_blocked_reason") or "page_redirect_blocked")
        metadata = parse_public_metadata(text_body) if result.body and not blocked else {}
        metadata_richness = classify_metadata_richness(metadata, blocked=blocked)
        snippet = text_body[:2000]
        preview_candidates = [url for url in metadata.get("preview_image_candidates", []) if isinstance(url, str)]
        preview_result = (
            fetch_preview_from_candidates(preview_candidates, output_dir=preview_dir, index=index, timeout_seconds=timeout_seconds, http_get=preview_http_get)
            if preview_candidates and not blocked
            else {
                "status": "reference_unavailable" if not blocked else "pixiv_page_blocked",
                "image_path": None,
                "reason": blocked_reason or "no_preview_url_candidate",
                "host_policy_status": "page_blocked" if blocked else "no_preview_url_candidate",
                "preview_candidates_total": len(preview_candidates),
                "preview_candidates_skipped_unexpected_host": 0,
                "preview_candidates_attempted_allowed": 0,
            }
        )
        correspondence = (
            verify_correspondence(sample, preview_result, storage_root=storage_root)
            if not blocked
            else {"status": "pixiv_page_blocked", "scores": None, "reason": blocked_reason}
        )
        page_results.append(
            {
                "media_id": sample["media_id"],
                "pixiv_work_id": work_id,
                "page_index": int(match["page_index"]),
                "url": artwork_url,
                "http_status": result.status,
                "network_attempted": True,
                "network_attempt_count": int(page_fetch_policy.get("network_attempt_count") or 1),
                "final_url": result.final_url,
                "content_type": result.content_type,
                "content_length_header": result.content_length_header,
                "redirected": result.final_url != artwork_url,
                **page_fetch_policy,
                "error": result.error,
                "blocked": blocked,
                "blocked_reason": blocked_reason,
                "metadata": metadata,
                "metadata_richness": metadata_richness,
                "preview_result": preview_result,
                "response_snippet": snippet,
                "request_headers": build_safe_headers(accept="text/html"),
                "cookies_sent": False,
                "referer_sent": False,
                "browser_automation": False,
            }
        )
        correspondence_results.append(
            {
                "media_id": sample["media_id"],
                "pixiv_work_id": work_id,
                "page_index": int(match["page_index"]),
                "preview_status": preview_result.get("status"),
                **correspondence,
            }
        )
        if blocked and should_stop_after_blocked_reason(blocked_reason):
            stop_reason = blocked_reason or "pixiv_page_blocked"
        if not stop_reason and index < len(selected) and delay_seconds > 0:
            time.sleep(delay_seconds)
    return page_results, correspondence_results


def booru_lookup_policy_result(*, enabled: bool = False) -> dict[str, Any]:
    if not enabled:
        return {
            "status": "no_upload_booru_lookup_policy_blocked",
            "requests_attempted": 0,
            "provider": None,
            "reason": "P1 inspected the option but did not enable a no-upload booru source-URL lookup route by default; exact source query syntax and disclosure policy should be approved separately before calls.",
            "source_url_query_public": False,
        }
    return {
        "status": "no_upload_booru_lookup_policy_blocked",
        "requests_attempted": 0,
        "provider": None,
        "reason": "enable flag is reserved for a future approved adapter; P1 keeps booru lookup non-calling.",
        "source_url_query_public": False,
    }


def aggregate_public_results(
    page_results: list[dict[str, Any]],
    correspondence_results: list[dict[str, Any]],
) -> dict[str, Any]:
    request_count = sum(
        int(item.get("network_attempt_count") or 1)
        for item in page_results
        if item.get("network_attempted") or item.get("http_status") is not None
    )
    http_statuses = Counter(str(item.get("http_status")) for item in page_results if item.get("http_status") is not None)
    network_errors = Counter(
        str(item.get("error") or item.get("blocked_reason") or "status_none")
        for item in page_results
        if (item.get("network_attempted") or item.get("http_status") is not None) and item.get("http_status") is None
    )
    metadata_richness = Counter(item.get("metadata_richness") or "not_attempted" for item in page_results)
    preview_statuses = Counter(item.get("preview_result", {}).get("status") or "not_attempted" for item in page_results)
    preview_host_policy = Counter(item.get("preview_result", {}).get("host_policy_status") or "not_attempted" for item in page_results)
    preview_candidate_totals = {
        "preview_candidates_total": sum(int(item.get("preview_result", {}).get("preview_candidates_total") or 0) for item in page_results),
        "preview_candidates_skipped_unexpected_host": sum(
            int(item.get("preview_result", {}).get("preview_candidates_skipped_unexpected_host") or 0) for item in page_results
        ),
        "preview_candidates_attempted_allowed": sum(
            int(item.get("preview_result", {}).get("preview_candidates_attempted_allowed") or 0) for item in page_results
        ),
    }
    correspondence_statuses = Counter(item.get("status") or "not_attempted" for item in correspondence_results)
    field_availability = Counter()
    hosts = Counter()
    for item in page_results:
        final_url = item.get("final_url")
        if final_url:
            host = urllib.parse.urlparse(final_url).hostname
            if host:
                hosts[host] += 1
        for field in item.get("metadata", {}).get("metadata_fields_found", []):
            field_availability[field] += 1
    return {
        "pixiv_page_probe": {
            "requests_attempted": request_count,
            "network_attempts_including_failures": request_count,
            "http_status_distribution": dict(sorted(http_statuses.items())),
            "status_none_count": sum(network_errors.values()),
            "network_error_distribution": dict(sorted(network_errors.items())),
            "final_url_host_distribution": dict(sorted(hosts.items())),
            "blocked_count": sum(1 for item in page_results if item.get("blocked")),
            "stopped_early": any(item.get("status") == "not_attempted_after_stop" for item in page_results),
            "public_exact_urls": False,
        },
        "metadata_availability": {
            "metadata_richness_distribution": dict(sorted(metadata_richness.items())),
            "field_availability_counts": dict(sorted(field_availability.items())),
        },
        "preview_availability": {
            "preview_status_distribution": dict(sorted(preview_statuses.items())),
            "preview_candidate_host_policy_distribution": dict(sorted(preview_host_policy.items())),
            "preview_candidate_counts": preview_candidate_totals,
        },
        "correspondence_verification": {
            "result_distribution": dict(sorted(correspondence_statuses.items())),
            "threshold_policy_version": "phase44p1-pilot-v1-not-production",
        },
    }


def future_recommendation(aggregate: dict[str, Any], booru_policy: dict[str, Any]) -> dict[str, Any]:
    correspondence = aggregate["correspondence_verification"]["result_distribution"]
    metadata = aggregate["metadata_availability"]["metadata_richness_distribution"]
    non_auto_requires_validation = any(
        correspondence.get(key, 0) > 0
        for key in ("auto_rejected_mismatch", "preview_fetch_blocked", "uncertain_needs_manual_or_lookup", "metadata_only_no_reference")
    )
    if correspondence.get("auto_verified_high_confidence", 0) > 0:
        route = "Phase 4.4-P2 - Pixiv LocalSourceHint Persistence for Verified Source Priors"
        reason = (
            "At least one sample reached auto_verified_high_confidence, but non-auto-verified rows still require manual validation before P2."
            if non_auto_requires_validation
            else "At least one sample reached auto_verified_high_confidence."
        )
    elif any(key in metadata for key in ("rich_structured_metadata", "partial_metadata_title_artist")):
        route = "bounded Pixiv metadata extraction / verification follow-up before persistence"
        reason = "Public page metadata appears useful but correspondence verification did not yet produce enough high-confidence items."
    elif booru_policy.get("status") == "completed":
        route = "no-upload booru source URL metadata adapter"
        reason = "Booru lookup worked better than Pixiv public metadata."
    else:
        route = "another bounded reference acquisition route or local-only filename_token_only SourcePrior design"
        reason = "Pixiv public-page or preview route remained blocked/limited for this sample."
    return {
        "recommended_next_route": route,
        "reason": reason,
        "p2_should_persist_local_source_hint": correspondence.get("auto_verified_high_confidence", 0) > 0 and not non_auto_requires_validation,
        "p2_should_wait_for_manual_validation": bool(non_auto_requires_validation),
        "p2_requires_db_write_approval": True,
        "filename_token_only_is_not_confirmed_evidence": True,
    }


def assert_public_payload_safe(payload: Any, *, private_markers: Iterable[str] = ()) -> None:
    text_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if LOCAL_PATH_RE.search(text_payload):
        raise PrivacyBlocked("public_payload_contains_local_path")
    if SECRET_TEXT_RE.search(text_payload):
        raise PrivacyBlocked("public_payload_contains_secret_like_text")
    for marker in private_markers:
        if marker and marker in text_payload:
            raise PrivacyBlocked(f"public_payload_contains_private_marker:{marker[:8]}")
    if PIXIV_ID_IN_URL_RE.search(text_payload):
        raise PrivacyBlocked("public_payload_contains_exact_pixiv_artwork_url")


def build_public_summary(
    *,
    generated_at: str,
    identity: dict[str, Any],
    artifact_labels: dict[str, Any],
    extraction_summary: dict[str, Any],
    sample_summary: dict[str, Any],
    network_policy: dict[str, Any],
    aggregate: dict[str, Any],
    booru_policy: dict[str, Any],
    reviewer_carry_forward: list[dict[str, str]],
    manual_validation_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_corr = aggregate["correspondence_verification"]["result_distribution"]
    current_mismatch_count = int(current_corr.get("auto_rejected_mismatch", 0))
    previous_mismatch_count = int(PRE_REVIEW_CORRESPONDENCE_BASELINE["auto_rejected_mismatch"])
    return {
        "phase": PHASE,
        "title": "Pixiv Source-Prior Live Reference / Metadata / Correspondence Pilot",
        "generated_at": generated_at,
        "why_p1_exists": "P0 showed significant filename prior coverage and designed the gate; P1 makes a bounded real public-page probe attempt without persistence.",
        "p0_correction": "P0 reference_lookup_policy_blocked was a policy result, not proof the Pixiv route is invalid.",
        "db_identity": identity,
        "reviewer_carry_forward": reviewer_carry_forward,
        "artifact_labels": artifact_labels,
        "source_prior_extraction_metrics": extraction_summary,
        "sample_selection": sample_summary,
        "pixiv_public_page_probe_policy": network_policy,
        "request_counts_and_status_distribution": aggregate["pixiv_page_probe"],
        "metadata_availability_distribution": aggregate["metadata_availability"],
        "preview_reference_availability_distribution": aggregate["preview_availability"],
        "correspondence_verification_distribution": aggregate["correspondence_verification"],
        "optional_no_upload_booru_source_url_lookup": booru_policy,
        "manual_validation_pack": manual_validation_summary or {},
        "pre_review_result_baseline": {
            "correspondence_result_distribution": PRE_REVIEW_CORRESPONDENCE_BASELINE,
            "mismatch_count_changed_after_fixes": current_mismatch_count != previous_mismatch_count,
            "previous_mismatch_count": previous_mismatch_count,
            "current_mismatch_count": current_mismatch_count,
        },
        "future_persistence_recommendation": future_recommendation(aggregate, booru_policy),
        "privacy_and_safety_confirmation": {
            "db_write": False,
            "db_migration": False,
            "provider_cache_write": False,
            "entity_evidence_write": False,
            "media_entity_candidate_write": False,
            "media_entity_assignment_write": False,
            "confirmed_assignment": False,
            "automatic_entity_creation": False,
            "media_tags_mutation": False,
            "tag_translation_mutation": False,
            "localization_execution": False,
            "entity_resolver": False,
            "broad_similarity_or_clustering": False,
            "source_or_icloud_mutation": False,
            "app_managed_storage_mutation": False,
            "original_image_download": False,
            "original_image_upload": False,
            "cookies_or_login": False,
            "browser_automation": False,
            "hotlink_or_referer_bypass": False,
            "high_volume_requests": False,
            "public_exact_media_to_pixiv_mapping": False,
            "push_main": False,
            "merge": False,
        },
    }


def build_markdown_report(summary: dict[str, Any]) -> str:
    page = summary["request_counts_and_status_distribution"]
    metadata = summary["metadata_availability_distribution"]
    preview = summary["preview_reference_availability_distribution"]
    corr = summary["correspondence_verification_distribution"]
    lines = [
        "# Phase 4.4-P1 - Pixiv Live Reference / Metadata / Correspondence Pilot",
        "",
        "## Why P1 Exists",
        "",
        summary["why_p1_exists"],
        "",
        "P0's `reference_lookup_policy_blocked` result was a policy stop, not evidence that the Pixiv filename-prior route is technically invalid.",
        "",
        "## Sample Selection",
        "",
        f"- Selected sample size: `{summary['sample_selection']['selected_count']}`.",
        f"- Requested sample size: `{summary['sample_selection'].get('requested_sample_size')}`.",
        f"- Anime-only sample: `{summary['sample_selection'].get('anime_only')}`.",
        f"- Insufficient anime candidates: `{summary['sample_selection'].get('insufficient_anime_candidates')}`.",
        f"- Strategy: `{summary['sample_selection']['selection_strategy']}`.",
        f"- Category counts: `{json.dumps(summary['sample_selection']['category_counts'], sort_keys=True)}`.",
        f"- Page case distribution: `{json.dumps(summary['sample_selection']['page_case_distribution'], sort_keys=True)}`.",
        "- Exact sample details are local-only.",
        "",
        "## Public Pixiv Page Probe Policy",
        "",
        f"- Concurrency: `{summary['pixiv_public_page_probe_policy']['concurrency']}`.",
        f"- Timeout seconds: `{summary['pixiv_public_page_probe_policy']['timeout_seconds']}`.",
        f"- Delay seconds: `{summary['pixiv_public_page_probe_policy']['delay_seconds']}`.",
        "- Cookies/login/browser automation/referer bypass: `False`.",
        "- Stop condition: 403, 429, login/captcha/consent/anti-bot marker.",
        "",
        "## Pixiv Public-Page Probe Result",
        "",
        f"- Requests attempted: `{page['requests_attempted']}`.",
        f"- Network attempts including failures: `{page.get('network_attempts_including_failures', page['requests_attempted'])}`.",
        f"- HTTP status distribution: `{json.dumps(page['http_status_distribution'], sort_keys=True)}`.",
        f"- Status-none count: `{page.get('status_none_count', 0)}`.",
        f"- Network error distribution: `{json.dumps(page.get('network_error_distribution', {}), sort_keys=True)}`.",
        f"- Final URL host distribution: `{json.dumps(page['final_url_host_distribution'], sort_keys=True)}`.",
        f"- Blocked count: `{page['blocked_count']}`.",
        f"- Stopped early: `{page['stopped_early']}`.",
        "",
        "## Metadata Availability",
        "",
        f"- Metadata richness distribution: `{json.dumps(metadata['metadata_richness_distribution'], sort_keys=True)}`.",
        f"- Field availability counts: `{json.dumps(metadata['field_availability_counts'], sort_keys=True)}`.",
        "",
        "## Preview / Reference Availability",
        "",
        f"- Preview status distribution: `{json.dumps(preview['preview_status_distribution'], sort_keys=True)}`.",
        f"- Preview candidate host policy distribution: `{json.dumps(preview.get('preview_candidate_host_policy_distribution', {}), sort_keys=True)}`.",
        f"- Preview candidate counts: `{json.dumps(preview.get('preview_candidate_counts', {}), sort_keys=True)}`.",
        "",
        "## Correspondence Verification",
        "",
        f"- Result distribution: `{json.dumps(corr['result_distribution'], sort_keys=True)}`.",
        f"- Threshold policy: `{corr['threshold_policy_version']}`.",
        f"- Mismatch count changed after reviewer fixes: `{summary['pre_review_result_baseline']['mismatch_count_changed_after_fixes']}`.",
        f"- Previous/current mismatch count: `{summary['pre_review_result_baseline']['previous_mismatch_count']}` / `{summary['pre_review_result_baseline']['current_mismatch_count']}`.",
        "",
        "## Manual Validation Pack",
        "",
        f"- Generated: `{summary.get('manual_validation_pack', {}).get('generated', False)}`.",
        f"- Items needing manual validation: `{summary.get('manual_validation_pack', {}).get('items_needing_manual_validation', 0)}`.",
        f"- Reason bucket distribution: `{json.dumps(summary.get('manual_validation_pack', {}).get('mismatch_reason_bucket_distribution', {}), sort_keys=True)}`.",
        "",
        "## Optional No-Upload Booru Lookup",
        "",
        f"- Status: `{summary['optional_no_upload_booru_source_url_lookup']['status']}`.",
        f"- Requests attempted: `{summary['optional_no_upload_booru_source_url_lookup']['requests_attempted']}`.",
        f"- Reason: {summary['optional_no_upload_booru_source_url_lookup']['reason']}",
        "",
        "## Future Persistence Recommendation",
        "",
        f"- Recommended next route: `{summary['future_persistence_recommendation']['recommended_next_route']}`.",
        f"- Reason: {summary['future_persistence_recommendation']['reason']}",
        f"- P2 should persist LocalSourceHint now: `{summary['future_persistence_recommendation']['p2_should_persist_local_source_hint']}`.",
        f"- P2 should wait for manual validation: `{summary['future_persistence_recommendation'].get('p2_should_wait_for_manual_validation')}`.",
        "- Any P2 persistence still requires explicit DB-write approval and must not treat filename-token-only rows as confirmed evidence.",
        "",
        "## Local Artifacts",
        "",
    ]
    for key, value in summary["artifact_labels"].items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Privacy and Safety Confirmation", ""])
    for key, value in summary["privacy_and_safety_confirmation"].items():
        lines.append(f"- {key}: `{value}`")
    lines.append("")
    return "\n".join(lines)


def _markdown_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _html_image(path_text: str | None, *, alt: str) -> str:
    if not path_text:
        return ""
    path = Path(path_text)
    if not path.exists():
        return f"<code>{html.escape(path_text)}</code>"
    return f'<img src="{html.escape(path.resolve().as_uri())}" alt="{html.escape(alt)}" style="max-width:320px;max-height:320px;object-fit:contain;border:1px solid #ccc">'


def _likely_mismatch_reason(page_result: dict[str, Any], corr: dict[str, Any]) -> str:
    status = corr.get("status")
    if status == "auto_verified_high_confidence":
        return "not_needed_auto_verified"
    preview_result = page_result.get("preview_result") or {}
    preview_status = preview_result.get("status")
    if page_result.get("blocked") or preview_status in {"pixiv_page_blocked", "reference_unavailable"}:
        if page_result.get("metadata_richness") in {None, "", "metadata_limited_requires_followup", "blocked"}:
            return "metadata_parse_insufficient"
        return "unsupported_or_unclear"
    try:
        page_index = int(page_result.get("page_index") or 0)
    except (TypeError, ValueError):
        page_index = 0
    if page_index > 0:
        return "page_index_mismatch_possible"
    scores = corr.get("scores") or {}
    if not scores:
        return "unsupported_or_unclear"
    aspect_delta = float(scores.get("aspect_ratio_delta") or 0)
    ahash_distance = int(scores.get("ahash_distance") or 0)
    dhash_distance = int(scores.get("dhash_distance") or 0)
    color_distance = float(scores.get("average_color_distance") or 0)
    if aspect_delta >= 0.18 and ahash_distance < 26 and dhash_distance < 26:
        return "preview_crop_or_thumbnail_variant"
    if status == "uncertain_needs_manual_or_lookup" or (
        aspect_delta <= 0.08 and ahash_distance <= 14 and dhash_distance <= 16 and color_distance <= 70
    ):
        return "threshold_too_strict_possible"
    if ahash_distance >= 26 or dhash_distance >= 26 or color_distance >= 130:
        return "true_mismatch_possible"
    return "unsupported_or_unclear"


def build_manual_validation_rows(
    selected: list[dict[str, Any]],
    page_results: list[dict[str, Any]],
    correspondence: list[dict[str, Any]],
    *,
    storage_root: Path,
) -> list[dict[str, Any]]:
    selected_by_media = {int(item["media_id"]): item for item in selected}
    corr_by_media = {int(item["media_id"]): item for item in correspondence if item.get("media_id") is not None}
    rows: list[dict[str, Any]] = []
    for row_number, page_result in enumerate(page_results, start=1):
        media_id = int(page_result["media_id"])
        sample = selected_by_media.get(media_id, {})
        local_media = sample.get("local_media", {})
        corr = corr_by_media.get(media_id, {})
        metadata = page_result.get("metadata") or {}
        preview_result = page_result.get("preview_result") or {}
        local_image_path = resolve_media_image_path(local_media, storage_root)
        scores = corr.get("scores") or {}
        likely_bucket = _likely_mismatch_reason(page_result, corr)
        manual_needed = corr.get("status") != "auto_verified_high_confidence"
        rows.append(
            {
                "row_number": row_number,
                "manual_validation_needed": manual_needed,
                "media_id": media_id,
                "pixiv_work_id": page_result.get("pixiv_work_id"),
                "page_index": page_result.get("page_index"),
                "local_filename_basename": local_media.get("filename_basename") or local_media.get("filename") or "",
                "local_stored_thumbnail_path": local_media.get("thumbnail_path") or "",
                "local_stored_media_path": local_media.get("path") or "",
                "local_resolved_image_path": str(local_image_path) if local_image_path else "",
                "pixiv_artwork_url": page_result.get("url") or "",
                "fetched_preview_local_path": preview_result.get("image_path") or "",
                "preview_url_host": preview_result.get("preview_url_host") or "",
                "preview_final_url_host": preview_result.get("final_url_host") or "",
                "metadata_richness": page_result.get("metadata_richness") or "",
                "title": metadata.get("title") or "",
                "description": metadata.get("description") or "",
                "artist_user_name": metadata.get("artist_user_name") or "",
                "artist_user_id": metadata.get("artist_user_id") or "",
                "tags": ";".join(str(item) for item in metadata.get("tags") or []),
                "page_count": metadata.get("page_count") if metadata.get("page_count") is not None else "",
                "aspect_ratio_delta": scores.get("aspect_ratio_delta", ""),
                "ahash_distance": scores.get("ahash_distance", ""),
                "dhash_distance": scores.get("dhash_distance", ""),
                "average_color_distance": scores.get("average_color_distance", ""),
                "preview_status": preview_result.get("status") or "",
                "final_auto_status": corr.get("status") or "",
                "likely_mismatch_reason_bucket": likely_bucket,
                "user_visual_match": "",
                "user_notes": "",
            }
        )
    return rows


def manual_validation_public_summary(rows: list[dict[str, Any]], paths: OutputPaths) -> dict[str, Any]:
    bucket_counts = Counter(
        row["likely_mismatch_reason_bucket"]
        for row in rows
        if row.get("manual_validation_needed") and row.get("likely_mismatch_reason_bucket")
    )
    return {
        "generated": True,
        "sheet_md": public_path_label(paths.manual_validation_sheet_md),
        "sheet_csv": public_path_label(paths.manual_validation_sheet_csv),
        "contact_sheet_html": public_path_label(paths.manual_validation_contact_sheet_html),
        "contact_sheet_md": public_path_label(paths.manual_validation_contact_sheet_md),
        "total_items": len(rows),
        "items_needing_manual_validation": sum(1 for row in rows if row.get("manual_validation_needed")),
        "mismatch_reason_bucket_distribution": dict(sorted(bucket_counts.items())),
        "contains_exact_private_details": True,
        "exact_private_details_public": False,
    }


def build_manual_validation_sheet_md(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Phase 4.4-P1 Pixiv Manual Validation Sheet",
        "",
        "Local ignored artifact. It contains exact media IDs, Pixiv work IDs, filenames, URLs, and local paths.",
        "",
        "| row | media_id | pixiv_work_id | page_index | local_filename_basename | local_image_path | pixiv_url | reference_path | preview_host | metadata | title | artist | page_count | aspect_delta | aHash | dHash | color | auto_status | likely_bucket | user_visual_match | user_notes |",
        "|---:|---:|---|---:|---|---|---|---|---|---|---|---|---:|---:|---:|---:|---:|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {row_number} | {media_id} | {pixiv_work_id} | {page_index} | {local_filename_basename} | {local_resolved_image_path} | {pixiv_artwork_url} | {fetched_preview_local_path} | {preview_url_host} | {metadata_richness} | {title} | {artist_user_name} | {page_count} | {aspect_ratio_delta} | {ahash_distance} | {dhash_distance} | {average_color_distance} | {final_auto_status} | {likely_mismatch_reason_bucket} |  |  |".format(
                **{key: _markdown_cell(value) for key, value in row.items()}
            )
        )
    lines.extend(
        [
            "",
            "## Manual Decision Columns",
            "",
            "- `user_visual_match`: fill with `yes`, `no`, or `uncertain`.",
            "- `user_notes`: record page-index, preview crop, threshold, derivative, or true mismatch observations.",
            "",
        ]
    )
    return "\n".join(lines)


def build_manual_validation_sheet_csv(rows: list[dict[str, Any]]) -> str:
    fieldnames = [
        "row_number",
        "manual_validation_needed",
        "media_id",
        "pixiv_work_id",
        "page_index",
        "local_filename_basename",
        "local_stored_thumbnail_path",
        "local_stored_media_path",
        "local_resolved_image_path",
        "pixiv_artwork_url",
        "fetched_preview_local_path",
        "preview_url_host",
        "preview_final_url_host",
        "metadata_richness",
        "title",
        "description",
        "artist_user_name",
        "artist_user_id",
        "tags",
        "page_count",
        "aspect_ratio_delta",
        "ahash_distance",
        "dhash_distance",
        "average_color_distance",
        "preview_status",
        "final_auto_status",
        "likely_mismatch_reason_bucket",
        "user_visual_match",
        "user_notes",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def build_manual_contact_sheet_html(rows: list[dict[str, Any]]) -> str:
    parts = [
        "<!doctype html>",
        "<html><head><meta charset=\"utf-8\"><title>Phase 4.4-P1 Pixiv Manual Validation Contact Sheet</title>",
        "<style>body{font-family:Arial,sans-serif;margin:24px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:8px;vertical-align:top}code{white-space:pre-wrap}</style>",
        "</head><body>",
        "<h1>Phase 4.4-P1 Pixiv Manual Validation Contact Sheet</h1>",
        "<p>Local ignored artifact with exact private details.</p>",
        "<table><thead><tr><th>Row</th><th>Local</th><th>Reference</th><th>Decision</th></tr></thead><tbody>",
    ]
    for row in rows:
        local_img = _html_image(row.get("local_resolved_image_path"), alt=f"local row {row['row_number']}")
        ref_img = _html_image(row.get("fetched_preview_local_path"), alt=f"reference row {row['row_number']}")
        parts.append(
            "<tr>"
            f"<td>{html.escape(str(row['row_number']))}<br><code>media_id={html.escape(str(row['media_id']))}</code><br><code>pixiv={html.escape(str(row['pixiv_work_id']))}_p{html.escape(str(row['page_index']))}</code></td>"
            f"<td>{local_img}<br><code>{html.escape(str(row.get('local_resolved_image_path') or row.get('local_stored_thumbnail_path') or ''))}</code></td>"
            f"<td>{ref_img}<br><a href=\"{html.escape(str(row['pixiv_artwork_url']))}\">Pixiv artwork page</a><br><code>{html.escape(str(row.get('fetched_preview_local_path') or ''))}</code></td>"
            f"<td><strong>{html.escape(str(row['final_auto_status']))}</strong><br><code>{html.escape(str(row['likely_mismatch_reason_bucket']))}</code><br>user_visual_match: yes/no/uncertain<br>notes:</td>"
            "</tr>"
        )
    parts.extend(["</tbody></table>", "</body></html>"])
    return "\n".join(parts) + "\n"


def build_manual_contact_sheet_md(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Phase 4.4-P1 Pixiv Manual Validation Contact Sheet",
        "",
        "Local ignored artifact. Open the HTML contact sheet for side-by-side image previews when possible.",
        "",
        "| row | local image | reference image | auto status | likely bucket |",
        "|---:|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {row} | {local} | {ref} | {status} | {bucket} |".format(
                row=_markdown_cell(row.get("row_number")),
                local=_markdown_cell(row.get("local_resolved_image_path") or row.get("local_stored_thumbnail_path")),
                ref=_markdown_cell(row.get("fetched_preview_local_path")),
                status=_markdown_cell(row.get("final_auto_status")),
                bucket=_markdown_cell(row.get("likely_mismatch_reason_bucket")),
            )
        )
    return "\n".join(lines) + "\n"


def build_metadata_sheet_md(page_results: list[dict[str, Any]], correspondence: list[dict[str, Any]]) -> str:
    corr_by_media = {item.get("media_id"): item for item in correspondence}
    lines = [
        "# Phase 4.4-P1 Pixiv Metadata Local Sheet",
        "",
        "Local ignored artifact. Exact Pixiv IDs, URLs, metadata snippets, and media IDs must not be copied into public reports.",
        "",
        "| media_id | pixiv_work_id | page_index | http_status | metadata_richness | preview_status | correspondence_status | title | artist | tags_count | notes |",
        "|---:|---|---:|---:|---|---|---|---|---|---:|---|",
    ]
    for item in page_results:
        metadata = item.get("metadata") or {}
        corr = corr_by_media.get(item.get("media_id"), {})
        lines.append(
            "| {media_id} | {work_id} | {page_index} | {status} | {richness} | {preview} | {corr} | {title} | {artist} | {tags_count} | |".format(
                media_id=item.get("media_id"),
                work_id=item.get("pixiv_work_id"),
                page_index=item.get("page_index"),
                status=item.get("http_status") or "",
                richness=item.get("metadata_richness") or "",
                preview=(item.get("preview_result") or {}).get("status") or "",
                corr=corr.get("status") or "",
                title=str(metadata.get("title") or "").replace("|", "\\|"),
                artist=str(metadata.get("artist_user_name") or "").replace("|", "\\|"),
                tags_count=len(metadata.get("tags") or []),
            )
        )
    return "\n".join(lines) + "\n"


def build_metadata_sheet_csv(page_results: list[dict[str, Any]], correspondence: list[dict[str, Any]]) -> str:
    corr_by_media = {item.get("media_id"): item for item in correspondence}
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "media_id",
            "pixiv_work_id",
            "page_index",
            "http_status",
            "metadata_richness",
            "preview_status",
            "correspondence_status",
            "title",
            "artist",
            "tags_count",
        ],
        lineterminator="\n",
    )
    writer.writeheader()
    for item in page_results:
        metadata = item.get("metadata") or {}
        corr = corr_by_media.get(item.get("media_id"), {})
        writer.writerow(
            {
                "media_id": item.get("media_id"),
                "pixiv_work_id": item.get("pixiv_work_id"),
                "page_index": item.get("page_index"),
                "http_status": item.get("http_status") or "",
                "metadata_richness": item.get("metadata_richness") or "",
                "preview_status": (item.get("preview_result") or {}).get("status") or "",
                "correspondence_status": corr.get("status") or "",
                "title": metadata.get("title") or "",
                "artist": metadata.get("artist_user_name") or "",
                "tags_count": len(metadata.get("tags") or []),
            }
        )
    return output.getvalue()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--delay-seconds", type=float, default=DEFAULT_DELAY_SECONDS)
    parser.add_argument("--enable-booru-lookup", action="store_true")
    parser.add_argument("--report-md", default=str(REPORT_MD))
    parser.add_argument("--report-json", default=str(REPORT_JSON))
    parser.add_argument("--reference-details-json", default=str(LOCAL_REFERENCE_DETAILS_JSON))
    parser.add_argument("--correspondence-details-json", default=str(LOCAL_CORRESPONDENCE_DETAILS_JSON))
    parser.add_argument("--sheet-md", default=str(LOCAL_SHEET_MD))
    parser.add_argument("--sheet-csv", default=str(LOCAL_SHEET_CSV))
    parser.add_argument("--preview-dir", default=str(LOCAL_PREVIEW_DIR))
    parser.add_argument("--manual-validation-sheet-md", default=str(LOCAL_MANUAL_VALIDATION_SHEET_MD))
    parser.add_argument("--manual-validation-sheet-csv", default=str(LOCAL_MANUAL_VALIDATION_SHEET_CSV))
    parser.add_argument("--manual-validation-contact-sheet-html", default=str(LOCAL_MANUAL_VALIDATION_CONTACT_SHEET_HTML))
    parser.add_argument("--manual-validation-contact-sheet-md", default=str(LOCAL_MANUAL_VALIDATION_CONTACT_SHEET_MD))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    sample_size = max(1, min(int(args.sample_size), MAX_SAMPLE_SIZE))
    output_paths = resolve_output_paths(args)
    config = load_project_config()

    engine = create_engine(config.database_url)
    install_read_only_guard(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        identity = prove_db_identity(session, config)
        extraction_summary, private_details = p0.audit_pixiv_source_priors(session, approved_ids=p0.APPROVED_D1G_SAMPLE_IDS)
        media_ids = [int(item["media_id"]) for item in private_details.get("details", []) if item.get("matches")]
        media_by_id = {int(row.id): row for row in session.query(Media).filter(Media.id.in_(media_ids)).all()}
    finally:
        session.close()
        engine.dispose()

    sample_summary, selected = select_p1_sample(private_details, media_by_id, sample_size=sample_size)
    network_policy = build_network_policy(sample_size, args.delay_seconds, args.timeout_seconds)
    page_results, correspondence_results = probe_pixiv_pages(
        selected,
        timeout_seconds=args.timeout_seconds,
        delay_seconds=args.delay_seconds,
        preview_dir=output_paths.preview_dir,
        storage_root=config.storage_root,
    )
    booru_policy = booru_lookup_policy_result(enabled=bool(args.enable_booru_lookup))
    aggregate = aggregate_public_results(page_results, correspondence_results)
    manual_validation_rows = build_manual_validation_rows(selected, page_results, correspondence_results, storage_root=config.storage_root)
    manual_validation_summary = manual_validation_public_summary(manual_validation_rows, output_paths)
    reviewer_carry_forward = [
        {
            "finding": "Honor VIOLET_STORAGE_ROOT when loading runtime data/settings.json",
            "p1_status": "implemented_in_p1_runner_and_tests",
        },
        {
            "finding": "Public reports must record actual resolved local artifact paths when CLI output-path overrides are used",
            "p1_status": "implemented_as_repo_relative_or_redacted_public_labels_with_local_absolute_details_private",
        },
        {
            "finding": "Restrict preview fetches to approved Pixiv image hosts",
            "p1_status": "implemented_with_initial_and_redirect_host_allowlist_and_tests",
        },
        {
            "finding": "Count failed network attempts in public request total",
            "p1_status": "implemented_with_network_attempts_including_failures_and_tests",
        },
        {
            "finding": "Parse Pixiv preload metadata from meta tags",
            "p1_status": "implemented_with_meta_content_parser_and_malformed_json_tests",
        },
        {
            "finding": "Keep handoff baseline consistent",
            "p1_status": "implemented_in_current_handoff_table",
        },
        {
            "finding": "Validate artwork-page redirects before following them",
            "p1_status": "implemented_with_no_redirect_page_fetch_and_safe_redirect_policy_tests",
        },
        {
            "finding": "Require HTTPS for allowlisted preview hosts",
            "p1_status": "implemented_for_initial_preview_urls_and_redirect_targets_with_tests",
        },
        {
            "finding": "Try allowed preview candidates before blocking the row",
            "p1_status": "implemented_with_candidate_iteration_and_aggregate_candidate_counts",
        },
        {
            "finding": "Avoid aborting the sample on transient network errors",
            "p1_status": "implemented_as_per_item_network_error_with_continue_sample_tests",
        },
        {
            "finding": "Restrict fallback sampling to anime items",
            "p1_status": "implemented_with_anime_only_selection_and_insufficient_anime_reporting_tests",
        },
    ]
    public_summary = build_public_summary(
        generated_at=_now_iso(),
        identity=identity,
        artifact_labels=build_public_artifact_labels(output_paths),
        extraction_summary=extraction_summary,
        sample_summary=sample_summary,
        network_policy=network_policy,
        aggregate=aggregate,
        booru_policy=booru_policy,
        reviewer_carry_forward=reviewer_carry_forward,
        manual_validation_summary=manual_validation_summary,
    )
    private_markers = private_details.get("distinct_pixiv_work_ids", [])
    assert_public_payload_safe(public_summary, private_markers=private_markers)
    assert_public_payload_safe(build_markdown_report(public_summary), private_markers=private_markers)

    reference_details = {
        "phase": PHASE,
        "generated_at": public_summary["generated_at"],
        "config_private": {
            "code_root": str(config.project_root),
            "storage_root": str(config.storage_root),
            "settings_file": str(config.settings_file),
            "settings_source": config.settings_source,
            "db_password_included": False,
        },
        "artifact_paths_private": local_artifact_path_details(output_paths),
        "selected_sample_private": selected,
        "pixiv_page_results": page_results,
        "booru_lookup_policy": booru_policy,
        "contains_exact_pixiv_ids": True,
        "contains_exact_urls": True,
        "contains_local_absolute_paths": True,
        "cookies_sent": False,
        "referer_sent": False,
        "browser_automation": False,
    }
    correspondence_details = {
        "phase": PHASE,
        "generated_at": public_summary["generated_at"],
        "correspondence_results": correspondence_results,
        "contains_exact_pixiv_ids": True,
        "contains_local_absolute_paths": True,
        "threshold_policy_version": "phase44p1-pilot-v1-not-production",
    }
    write_json(output_paths.reference_details_json, reference_details)
    write_json(output_paths.correspondence_details_json, correspondence_details)
    write_text(output_paths.sheet_md, build_metadata_sheet_md(page_results, correspondence_results))
    write_text(output_paths.sheet_csv, build_metadata_sheet_csv(page_results, correspondence_results))
    write_text(output_paths.manual_validation_sheet_md, build_manual_validation_sheet_md(manual_validation_rows))
    write_text(output_paths.manual_validation_sheet_csv, build_manual_validation_sheet_csv(manual_validation_rows))
    write_text(output_paths.manual_validation_contact_sheet_html, build_manual_contact_sheet_html(manual_validation_rows))
    write_text(output_paths.manual_validation_contact_sheet_md, build_manual_contact_sheet_md(manual_validation_rows))
    write_json(output_paths.report_json, public_summary)
    write_text(output_paths.report_md, build_markdown_report(public_summary))
    print(
        json.dumps(
            {
                "status": "completed",
                "report_json": public_path_label(output_paths.report_json),
                "report_md": public_path_label(output_paths.report_md),
                "requests_attempted": aggregate["pixiv_page_probe"]["requests_attempted"],
                "db_write_allowed": False,
                "cookies": False,
                "browser_automation": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
