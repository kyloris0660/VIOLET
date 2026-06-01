"""Phase 4.4-P2R-F1 gallery-dl JSON metadata import pilot.

Lifecycle: phase-scoped operational runner with reusable parser helpers. It
consumes local gallery-dl JSON/JSONL only, optionally reads Media metadata in a
read-only DB session, writes public-safe reports plus ignored private artifacts,
and never writes DB rows or creates entity/source persistence records.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

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

PHASE = "4.4-P2R-F1"
TITLE = "gallery-dl JSON Metadata Import Pilot and External Adapter Readiness"
REPORT_MD = Path("docs/reports/phase-4.4p2r-f1-gallery-dl-json-metadata-import-pilot.md")
REPORT_JSON = Path("docs/reports/phase-4.4p2r-f1-gallery-dl-json-metadata-import-pilot-summary.json")
PRIVATE_DETAILS_JSON = Path(".local_manifests/phase-4.4p2r-f1-gallery-dl-json-import-details.json")
PRIVATE_SHEET_CSV = Path(".local_manifests/phase-4.4p2r-f1-gallery-dl-json-import-sheet.csv")
PRIVATE_SHEET_MD = Path(".local_manifests/phase-4.4p2r-f1-gallery-dl-json-import-sheet.md")
PRIVATE_RAW_DIR = Path(".local_manifests/phase-4.4p2r-f1-gallery-dl-json-import-raw")
PHASE_OUTPUT_DIR = Path(".local_manifests/phase-4.4p2r-f1-gallery-dl-json-import-pilot")
DOWNLOAD_DIR = Path(".local_manifests/phase-4.4p2r-f1-gallery-dl-json-import-downloads")
PILOT_DOWNLOAD_DIR = PHASE_OUTPUT_DIR / "downloads"

GALLERY_DL_METADATA_COMMAND_TEMPLATE = (
    'gallery-dl --dump-json --no-download "https://www.pixiv.net/artworks/<WORK_ID>"'
)
GALLERY_DL_MODULE_COMMAND_TEMPLATE = (
    'py -m gallery_dl --dump-json --no-download "https://www.pixiv.net/artworks/<WORK_ID>"'
)

WRITE_SQL_RE = re.compile(
    r"^\s*(insert|update|delete|merge|alter|drop|truncate|create|replace|grant|revoke|copy\s+.+\s+from|vacuum)\b",
    re.IGNORECASE | re.DOTALL,
)
LOCAL_PATH_RE = re.compile(
    r"(?i)((?<![a-z])[a-z]:[\\/]|\\\\[A-Za-z0-9_.-]+[\\/]|file://|/(users|home|root|mnt|volumes|workspace|tmp|var)(/|$))"
)
SECRET_TEXT_RE = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9._~+\-/]{8,}|access[_-]?token\s*[=:]|refresh[_-]?token\s*[=:]|"
    r"api[_-]?key\s*[=:]|authorization\s*[=:]|cookie\s*[=:]|sk-[A-Za-z0-9_-]{16,})"
)
SECRET_KEY_PATTERNS = (
    "apikey",
    "api_key",
    "access_token",
    "accesstoken",
    "refresh_token",
    "refreshtoken",
    "authorization",
    "bearer",
    "cookie",
    "password",
    "secret",
    "credential",
)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}
JSON_EXTENSIONS = {".json", ".jsonl", ".ndjson"}


class Phase44P2RF1Error(RuntimeError):
    pass


class MissingInputError(Phase44P2RF1Error):
    pass


class JsonInputError(Phase44P2RF1Error):
    pass


class OutputPathError(Phase44P2RF1Error):
    pass


class PrivacyBlocked(Phase44P2RF1Error):
    pass


class ReadOnlyViolation(Phase44P2RF1Error):
    pass


@dataclass(frozen=True)
class ProjectConfig:
    project_root: Path
    violet_env: str
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


@dataclass
class GalleryDlRawRecord:
    data: dict[str, Any]
    source_file: str
    source_line: int | None
    record_shape: str
    event_type: int | None = None


@dataclass
class ParseResult:
    records: list[GalleryDlRawRecord]
    files: list[Path]
    invalid_json_count: int = 0
    unsupported_shape_count: int = 0
    skipped_invalid_count: int = 0


@dataclass
class LocalPriorCandidate:
    media_id: int
    content_class: str
    work_id: str
    page_index: int
    source_fields: tuple[str, ...]
    basenames_private: tuple[str, ...] = ()


@dataclass
class LocalPriorIndex:
    by_work_page: dict[tuple[str, int], list[LocalPriorCandidate]]
    total_media_inspected: int
    total_prior_media: int
    total_prior_keys: int
    content_class_distribution: dict[str, int]


@dataclass
class PixivGalleryDlMetadataRecord:
    source_adapter: str = "gallery_dl_json"
    adapter_version: str | None = None
    extractor_category: str | None = None
    extractor_subcategory: str | None = None
    work_id: str | None = None
    page_index: int | None = None
    page_count: int | None = None
    title: str | None = None
    artist_name: str | None = None
    artist_id: str | None = None
    tags: tuple[str, ...] = ()
    translated_tags: tuple[str, ...] = ()
    caption: str | None = None
    canonical_url: str | None = None
    image_url_kinds_available: tuple[str, ...] = ()
    gallery_dl_filename: str | None = None
    metadata_richness: str = "unsupported_record_shape"
    local_match_status: str = "unsupported_record_shape"
    page_index_status: str = "not_checked"
    local_media_id_private: int | None = None
    duplicate_local_media_ids_private: tuple[int, ...] = ()
    privacy_level: str = "private_exact_mapping"
    eligible_for_future_local_source_hint: bool = False
    eligible_for_future_entity_candidate: bool = False
    evidence_strength_candidate: str = "not_evidence"
    db_write_allowed: bool = False
    record_shape: str = "unknown"
    source_file_private: str | None = None

    def to_private_dict(self) -> dict[str, Any]:
        return asdict(self)

    def public_projection(self) -> dict[str, Any]:
        return {
            "source_adapter": self.source_adapter,
            "adapter_version_present": self.adapter_version is not None,
            "extractor_category": self.extractor_category,
            "extractor_subcategory": self.extractor_subcategory,
            "work_id_present": self.work_id is not None,
            "page_index_present": self.page_index is not None,
            "page_count_present": self.page_count is not None,
            "title_present": bool(self.title),
            "artist_name_present": bool(self.artist_name),
            "artist_id_present": self.artist_id is not None,
            "tag_count": len(self.tags),
            "translated_tag_count": len(self.translated_tags),
            "caption_present": bool(self.caption),
            "image_url_kinds_available": list(self.image_url_kinds_available),
            "metadata_richness": self.metadata_richness,
            "local_match_status": self.local_match_status,
            "page_index_status": self.page_index_status,
            "privacy_level": self.privacy_level,
            "eligible_for_future_local_source_hint": self.eligible_for_future_local_source_hint,
            "eligible_for_future_entity_candidate": self.eligible_for_future_entity_candidate,
            "evidence_strength_candidate": self.evidence_strength_candidate,
            "db_write_allowed": False,
            "record_shape": self.record_shape,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _enum_label(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _normalize_key(value: Any) -> str:
    return re.sub(r"[\W_]+", "", str(value).lower())


def _is_secret_like_key(key: Any) -> bool:
    normalized = _normalize_key(key)
    return any(pattern.replace("_", "") in normalized for pattern in SECRET_KEY_PATTERNS)


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return "<external-private-input>"


def _coerce_json_safe(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, Path):
        return _rel(value)
    if isinstance(value, Mapping):
        return {str(key): _coerce_json_safe(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_coerce_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_coerce_json_safe(item) for item in value]
    return value


def assert_no_secret_like_payload(payload: Any) -> None:
    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if _is_secret_like_key(key):
                    raise PrivacyBlocked(f"secret_like_key_detected:{key}")
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str) and SECRET_TEXT_RE.search(value):
            raise PrivacyBlocked("secret_like_value_detected")

    visit(payload)


def assert_public_payload_safe(payload: Any, *, private_markers: Iterable[str] = ()) -> None:
    normalized = _coerce_json_safe(payload)
    markers = [str(marker) for marker in private_markers if marker]
    serialized = json.dumps(normalized, ensure_ascii=False, sort_keys=True, allow_nan=False)
    for marker in markers:
        if marker and marker in serialized:
            raise PrivacyBlocked(f"public_payload_contains_private_marker:{marker}")

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if _is_secret_like_key(key):
                    raise PrivacyBlocked(f"public_payload_contains_secret_key:{key}")
                if _normalize_key(key) in {"path", "filepath", "localpath", "absolutepath", "imagebytes", "rawimagebytes"}:
                    raise PrivacyBlocked(f"public_payload_contains_forbidden_key:{key}")
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(value):
                raise PrivacyBlocked("public_payload_contains_non_finite_number")
        elif isinstance(value, str):
            if LOCAL_PATH_RE.search(value):
                raise PrivacyBlocked("public_payload_contains_local_path")
            if SECRET_TEXT_RE.search(value):
                raise PrivacyBlocked("public_payload_contains_secret_like_value")

    visit(normalized)


def resolve_repo_path(path: str | Path) -> Path:
    value = Path(path)
    if not value.is_absolute():
        value = ROOT / value
    return value.resolve()


def is_under_path(child: Path, parent: Path) -> bool:
    child_resolved = child.resolve()
    parent_resolved = parent.resolve()
    try:
        child_resolved.relative_to(parent_resolved)
        return True
    except ValueError:
        return False


def require_under_path(child: Path, parent: Path, *, code: str) -> None:
    if not is_under_path(child, parent):
        raise OutputPathError(code)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, content: str, *, expected_parent: Path) -> None:
    require_under_path(path, ROOT / expected_parent, code="output_path_violation")
    ensure_parent(path)
    path.write_text(content, encoding="utf-8", newline="\n")


def write_json(path: Path, payload: Any, *, expected_parent: Path) -> None:
    require_under_path(path, ROOT / expected_parent, code="output_path_violation")
    ensure_parent(path)
    path.write_text(json.dumps(_coerce_json_safe(payload), indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def load_project_config() -> ProjectConfig:
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

    return ProjectConfig(
        project_root=ROOT,
        violet_env=os.environ.get("VIOLET_ENV", "development"),
        db_user=os.environ.get("POSTGRES_USER", "postgres"),
        db_password=os.environ.get("POSTGRES_PASSWORD", "password"),
        db_host=os.environ.get("POSTGRES_HOST", "localhost"),
        db_port=int(os.environ.get("POSTGRES_PORT", "5432")),
        db_name=os.environ.get("POSTGRES_DB", "blombooru"),
    )


def install_read_only_guard(engine: Any) -> None:
    @event.listens_for(engine, "before_cursor_execute")
    def _before_cursor_execute(_conn, _cursor, statement, _parameters, _context, _executemany):
        if WRITE_SQL_RE.search(str(statement)):
            raise ReadOnlyViolation("db_write_blocked")


def prove_db_identity(session: Session, config: ProjectConfig) -> dict[str, Any]:
    actual_db = session.execute(text("SELECT current_database()")).scalar()
    return {
        "violet_env": config.violet_env,
        "configured_db_host": config.db_host,
        "configured_db_port": config.db_port,
        "configured_db_user": config.db_user,
        "configured_db_name": config.db_name,
        "actual_db_name": str(actual_db),
        "db_sensitive_value_included": False,
        "db_read_only_guard_installed": True,
        "local_paths_redacted": True,
    }


def _json_files_from_input(input_path: Path) -> list[Path]:
    if not input_path.exists():
        raise MissingInputError("missing_gallery_dl_json_input")
    if input_path.is_file():
        if input_path.suffix.lower() not in JSON_EXTENSIONS:
            raise MissingInputError("missing_gallery_dl_json_input")
        return [input_path]
    files = sorted(path for path in input_path.rglob("*") if path.is_file() and path.suffix.lower() in JSON_EXTENSIONS)
    if not files:
        raise MissingInputError("missing_gallery_dl_json_input")
    return files


def read_json_text(file_path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return file_path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise JsonInputError(f"unsupported_json_text_encoding:{file_path}")


def _dict_from_gallery_dl_event(value: Sequence[Any]) -> tuple[dict[str, Any] | None, str, int | None]:
    if not value or not isinstance(value[0], int):
        return None, "unsupported_list_record", None
    event_type = int(value[0])
    if event_type == 2 and len(value) >= 2 and isinstance(value[1], Mapping):
        data = dict(value[1])
        data["_gallery_dl_event_type"] = event_type
        return data, "gallery_dl_directory_event", event_type
    if event_type == 3:
        event_url = next((item for item in value[1:] if isinstance(item, str) and item.startswith(("http://", "https://"))), None)
        event_payload = next((item for item in reversed(value[1:]) if isinstance(item, Mapping)), None)
        if event_payload is not None:
            data = dict(event_payload)
            data["_gallery_dl_event_type"] = event_type
            if event_url and "url" not in data:
                data["_gallery_dl_event_url"] = event_url
            return data, "gallery_dl_url_event", event_type
    event_payload = next((item for item in value[1:] if isinstance(item, Mapping)), None)
    if event_payload is not None:
        data = dict(event_payload)
        data["_gallery_dl_event_type"] = event_type
        return data, "gallery_dl_other_event", event_type
    return None, f"unsupported_gallery_dl_event_{event_type}", event_type


def _extract_raw_records(payload: Any, *, source_file: Path, source_line: int | None = None) -> tuple[list[GalleryDlRawRecord], int]:
    source_label = _rel(source_file)
    if isinstance(payload, Mapping):
        return [
            GalleryDlRawRecord(
                data=dict(payload),
                source_file=source_label,
                source_line=source_line,
                record_shape="dict_record",
            )
        ], 0
    if isinstance(payload, list):
        if payload and isinstance(payload[0], int):
            data, shape, event_type = _dict_from_gallery_dl_event(payload)
            if data is None:
                return [], 1
            return [
                GalleryDlRawRecord(
                    data=data,
                    source_file=source_label,
                    source_line=source_line,
                    record_shape=shape,
                    event_type=event_type,
                )
            ], 0
        records: list[GalleryDlRawRecord] = []
        unsupported = 0
        for item in payload:
            nested, nested_unsupported = _extract_raw_records(item, source_file=source_file, source_line=source_line)
            records.extend(nested)
            unsupported += nested_unsupported
        return records, unsupported
    return [], 1


def parse_gallery_dl_json_inputs(input_path: str | Path, *, skip_invalid: bool = False) -> ParseResult:
    path = resolve_repo_path(input_path)
    files = _json_files_from_input(path)
    result = ParseResult(records=[], files=files)
    for file_path in files:
        text_value = read_json_text(file_path)
        stripped = text_value.strip()
        if not stripped:
            continue
        parsed_as_whole = False
        if stripped[0] in "[{":
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                payload = None
            else:
                records, unsupported = _extract_raw_records(payload, source_file=file_path)
                result.records.extend(records)
                result.unsupported_shape_count += unsupported
                parsed_as_whole = True
        if parsed_as_whole:
            continue
        for line_no, line in enumerate(text_value.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                result.invalid_json_count += 1
                if skip_invalid:
                    result.skipped_invalid_count += 1
                    continue
                raise JsonInputError(f"invalid_gallery_dl_json:{file_path}:{line_no}:{exc.msg}") from exc
            records, unsupported = _extract_raw_records(payload, source_file=file_path, source_line=line_no)
            result.records.extend(records)
            result.unsupported_shape_count += unsupported
    if result.invalid_json_count and not skip_invalid:
        raise JsonInputError("invalid_gallery_dl_json")
    return result


def _first_present(data: Mapping[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        if name in data and data[name] not in (None, ""):
            return data[name]
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _as_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _page_index_from_text(value: Any) -> int | None:
    if not value:
        return None
    matches = p0.extract_pixiv_filename_prior_from_text(str(value))
    if matches:
        return int(matches[0]["page_index"])
    match = re.search(r"(?<!\d)_p(?P<page_index>\d+)(?!\d)", str(value))
    if match:
        return int(match.group("page_index"))
    return None


def _work_id_from_text(value: Any) -> str | None:
    if not value:
        return None
    matches = p0.extract_pixiv_filename_prior_from_text(str(value))
    if matches:
        return str(matches[0]["pixiv_work_id"])
    match = re.search(r"/artworks/(?P<work_id>[1-9]\d{5,11})(?:\D|$)", str(value))
    if match:
        return match.group("work_id")
    return None


def _extract_page_count(data: Mapping[str, Any]) -> int | None:
    page_count = _as_int(_first_present(data, ("page_count", "pageCount", "pages")))
    if page_count is not None:
        return page_count
    meta_pages = data.get("meta_pages")
    if isinstance(meta_pages, list):
        return len(meta_pages)
    count_value = _as_int(data.get("count"))
    return count_value


def _extract_artist(data: Mapping[str, Any]) -> tuple[str | None, str | None]:
    user = data.get("user")
    if isinstance(user, Mapping):
        name = _as_string(_first_present(user, ("name", "account", "user_name", "username")))
        user_id = _as_string(_first_present(user, ("id", "user_id", "userId")))
        if name or user_id:
            return name, user_id
    artist_name = _as_string(_first_present(data, ("artist", "artist_name", "user_name", "userName", "author", "author_name")))
    artist_id = _as_string(_first_present(data, ("artist_id", "user_id", "userId", "author_id")))
    return artist_name, artist_id


def _extract_tags_from_value(value: Any) -> tuple[list[str], list[str]]:
    tags: list[str] = []
    translated: list[str] = []
    if isinstance(value, str):
        return [value], []
    if isinstance(value, Mapping):
        name = _as_string(_first_present(value, ("name", "tag", "value")))
        if name:
            tags.append(name)
        translated_name = _as_string(_first_present(value, ("translated_name", "translatedName", "translation", "translated")))
        if translated_name:
            translated.append(translated_name)
        return tags, translated
    if isinstance(value, list):
        for item in value:
            item_tags, item_translated = _extract_tags_from_value(item)
            tags.extend(item_tags)
            translated.extend(item_translated)
    return tags, translated


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text_value = str(value).strip()
        if text_value and text_value not in seen:
            seen.add(text_value)
            result.append(text_value)
    return tuple(result)


def _extract_tags(data: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    tags, translated = _extract_tags_from_value(_first_present(data, ("tags", "tag", "tag_list")))
    explicit_translated, _ = _extract_tags_from_value(
        _first_present(data, ("translated_tags", "tag_translations", "translatedTags"))
    )
    translated.extend(explicit_translated)
    return _dedupe(tags), _dedupe(translated)


def _collect_image_url_kinds(value: Any, *, prefix: str = "") -> set[str]:
    kinds: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            normalized = key_text.lower()
            next_prefix = f"{prefix}.{key_text}" if prefix else key_text
            if "profile_image_urls" in next_prefix:
                continue
            if isinstance(item, str) and item.startswith(("http://", "https://")):
                if "url" in normalized or normalized in {"original", "regular", "small", "medium", "large"}:
                    kinds.add(next_prefix)
            elif isinstance(item, (Mapping, list)):
                kinds.update(_collect_image_url_kinds(item, prefix=next_prefix))
    elif isinstance(value, list):
        for item in value:
            kinds.update(_collect_image_url_kinds(item, prefix=prefix))
    return kinds


def _metadata_richness(record: PixivGalleryDlMetadataRecord) -> str:
    present = {
        "work_id": record.work_id is not None,
        "page_index": record.page_index is not None,
        "page_count": record.page_count is not None,
        "title": bool(record.title),
        "artist": bool(record.artist_name or record.artist_id),
        "tags": bool(record.tags),
        "image_urls": bool(record.image_url_kinds_available),
    }
    if not present["work_id"]:
        return "unsupported_record_shape"
    if present["title"] and present["artist"] and present["tags"] and present["page_count"]:
        return "rich_structured_metadata"
    if sum(1 for value in present.values() if value) >= 4:
        return "partial_structured_metadata"
    return "minimal_metadata"


def normalize_gallery_dl_record(raw: GalleryDlRawRecord, *, adapter_version: str | None = None) -> PixivGalleryDlMetadataRecord:
    data = raw.data
    assert_no_secret_like_payload(data)
    work_id = _as_string(_first_present(data, ("id", "illust_id", "illustId", "work_id", "workId")))
    if work_id is None:
        work_id = _work_id_from_text(_first_present(data, ("url", "_gallery_dl_event_url", "filename", "source_url")))
    page_index = _as_int(_first_present(data, ("page_index", "pageIndex", "num", "page_num", "pageNum")))
    if page_index is None:
        page_index = _page_index_from_text(_first_present(data, ("filename", "url", "_gallery_dl_event_url")))
    page_count = _extract_page_count(data)
    artist_name, artist_id = _extract_artist(data)
    tags, translated_tags = _extract_tags(data)
    filename = _as_string(_first_present(data, ("filename", "basename", "original_filename")))
    image_kinds = sorted(_collect_image_url_kinds(data))
    canonical_url = f"https://www.pixiv.net/artworks/{work_id}" if work_id else None
    record = PixivGalleryDlMetadataRecord(
        adapter_version=adapter_version,
        extractor_category=_as_string(data.get("category")),
        extractor_subcategory=_as_string(data.get("subcategory")),
        work_id=work_id,
        page_index=page_index,
        page_count=page_count,
        title=_as_string(_first_present(data, ("title", "illust_title"))),
        artist_name=artist_name,
        artist_id=artist_id,
        tags=tags,
        translated_tags=translated_tags,
        caption=_as_string(_first_present(data, ("caption", "description", "comment"))),
        canonical_url=canonical_url,
        image_url_kinds_available=tuple(image_kinds),
        gallery_dl_filename=filename,
        record_shape=raw.record_shape,
        source_file_private=raw.source_file,
    )
    record.metadata_richness = _metadata_richness(record)
    return record


def normalize_records(parse_result: ParseResult, *, adapter_version: str | None) -> list[PixivGalleryDlMetadataRecord]:
    return [normalize_gallery_dl_record(raw, adapter_version=adapter_version) for raw in parse_result.records]


def build_local_prior_index(db: Session) -> LocalPriorIndex:
    rows = db.query(Media).order_by(Media.id.asc()).all()
    grouped: dict[tuple[str, int], dict[int, LocalPriorCandidate]] = {}
    content_counts: Counter[str] = Counter()
    prior_media = 0

    for media in rows:
        media_had_prior = False
        content_class = _enum_label(media.content_class) or "unset"
        per_media_matches: dict[tuple[str, int], dict[str, Any]] = {}
        for source_field, _field_kind, value in p0.metadata_fields_for_pixiv(media):
            basename = p0._basename_from_metadata(value)
            for match in p0.extract_pixiv_filename_prior_from_text(value):
                key = (str(match["pixiv_work_id"]), int(match["page_index"]))
                entry = per_media_matches.setdefault(key, {"source_fields": set(), "basenames": set()})
                entry["source_fields"].add(source_field)
                if basename:
                    entry["basenames"].add(basename)
        if per_media_matches:
            prior_media += 1
            content_counts[content_class] += 1
            media_had_prior = True
        if media_had_prior:
            for (work_id, page_index), entry in per_media_matches.items():
                grouped.setdefault((work_id, page_index), {})[int(media.id)] = LocalPriorCandidate(
                    media_id=int(media.id),
                    content_class=content_class,
                    work_id=work_id,
                    page_index=page_index,
                    source_fields=tuple(sorted(entry["source_fields"])),
                    basenames_private=tuple(sorted(entry["basenames"])),
                )

    return LocalPriorIndex(
        by_work_page={key: list(value.values()) for key, value in grouped.items()},
        total_media_inspected=len(rows),
        total_prior_media=prior_media,
        total_prior_keys=len(grouped),
        content_class_distribution=dict(sorted(content_counts.items())),
    )


def join_records_to_local_priors(
    records: Sequence[PixivGalleryDlMetadataRecord],
    prior_index: LocalPriorIndex | None,
) -> tuple[list[PixivGalleryDlMetadataRecord], dict[str, Any]]:
    joined: list[PixivGalleryDlMetadataRecord] = []
    status_counts: Counter[str] = Counter()
    page_status_counts: Counter[str] = Counter()
    metadata_keys: set[tuple[str, int]] = set()

    for record in records:
        updated = replace(record)
        if not updated.work_id:
            updated.local_match_status = "unsupported_record_shape"
        elif updated.page_index is None:
            updated.local_match_status = "missing_page_index"
        else:
            metadata_keys.add((updated.work_id, int(updated.page_index)))
            if updated.page_count is not None and int(updated.page_index) >= int(updated.page_count):
                updated.local_match_status = "page_index_out_of_range"
            elif prior_index is None:
                updated.local_match_status = "local_prior_join_not_run"
            else:
                matches = prior_index.by_work_page.get((updated.work_id, int(updated.page_index)), [])
                if len(matches) == 1:
                    updated.local_match_status = "metadata_matches_local_filename_prior"
                    updated.local_media_id_private = matches[0].media_id
                elif len(matches) > 1:
                    updated.local_match_status = "duplicate_or_ambiguous_local_match"
                    updated.duplicate_local_media_ids_private = tuple(sorted(match.media_id for match in matches))
                else:
                    updated.local_match_status = "metadata_work_id_found_no_local_match"

        if updated.page_index is None:
            updated.page_index_status = "missing_page_index"
        elif updated.page_count is not None and int(updated.page_index) >= int(updated.page_count):
            updated.page_index_status = "page_index_out_of_range"
        elif updated.page_count is None:
            updated.page_index_status = "page_count_missing"
        else:
            updated.page_index_status = "page_index_within_page_count"

        updated.eligible_for_future_local_source_hint = updated.local_match_status == "metadata_matches_local_filename_prior"
        updated.eligible_for_future_entity_candidate = (
            updated.eligible_for_future_local_source_hint
            and updated.metadata_richness in {"rich_structured_metadata", "partial_structured_metadata"}
        )
        updated.evidence_strength_candidate = (
            "weak_source_metadata_candidate" if updated.eligible_for_future_entity_candidate else "not_evidence"
        )
        updated.db_write_allowed = False
        joined.append(updated)
        status_counts[updated.local_match_status] += 1
        page_status_counts[updated.page_index_status] += 1

    local_prior_without_metadata = 0
    if prior_index is not None:
        local_prior_without_metadata = len(set(prior_index.by_work_page) - metadata_keys)

    return joined, {
        "status_counts": dict(sorted(status_counts.items())),
        "page_index_status_counts": dict(sorted(page_status_counts.items())),
        "local_prior_without_metadata": local_prior_without_metadata,
        "local_prior_join_ran": prior_index is not None,
        "local_prior_total_keys": prior_index.total_prior_keys if prior_index else 0,
        "local_prior_total_media": prior_index.total_prior_media if prior_index else 0,
        "local_prior_total_media_inspected": prior_index.total_media_inspected if prior_index else 0,
        "local_prior_content_class_distribution": prior_index.content_class_distribution if prior_index else {},
    }


def detect_gallery_dl_environment() -> dict[str, Any]:
    commands = [
        ("gallery-dl", ["gallery-dl", "--version"]),
        ("py -m gallery_dl", ["py", "-m", "gallery_dl", "--version"]),
        ("python -m gallery_dl", ["python", "-m", "gallery_dl", "--version"]),
    ]
    attempts: list[dict[str, Any]] = []
    for label, command in commands:
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=15)
        except (FileNotFoundError, subprocess.SubprocessError) as exc:
            attempts.append({"entrypoint": label, "available": False, "error_class": exc.__class__.__name__})
            continue
        version = (completed.stdout or completed.stderr).strip().splitlines()
        if completed.returncode == 0 and version:
            return {
                "gallery_dl_available": True,
                "gallery_dl_version": version[0].strip(),
                "command_entrypoint": label,
                "bare_gallery_dl_on_path": label == "gallery-dl",
                "attempts": attempts + [{"entrypoint": label, "available": True}],
            }
        attempts.append({"entrypoint": label, "available": False, "returncode": completed.returncode})
    return {
        "gallery_dl_available": False,
        "gallery_dl_version": None,
        "command_entrypoint": None,
        "bare_gallery_dl_on_path": False,
        "attempts": attempts,
    }


def _private_markers(records: Sequence[PixivGalleryDlMetadataRecord]) -> list[str]:
    markers: set[str] = set()
    for record in records:
        for value in (
            record.work_id,
            record.canonical_url,
            record.gallery_dl_filename,
        ):
            if value:
                markers.add(str(value))
    return sorted(markers, key=len, reverse=True)


def field_availability(records: Sequence[PixivGalleryDlMetadataRecord]) -> dict[str, int]:
    return {
        "work_id": sum(1 for record in records if record.work_id),
        "page_index": sum(1 for record in records if record.page_index is not None),
        "title": sum(1 for record in records if record.title),
        "artist_name": sum(1 for record in records if record.artist_name),
        "artist_id": sum(1 for record in records if record.artist_id),
        "tags": sum(1 for record in records if record.tags),
        "translated_tags": sum(1 for record in records if record.translated_tags),
        "caption": sum(1 for record in records if record.caption),
        "page_count": sum(1 for record in records if record.page_count is not None),
        "image_url_kinds": sum(1 for record in records if record.image_url_kinds_available),
        "gallery_dl_filename": sum(1 for record in records if record.gallery_dl_filename),
        "extractor_category": sum(1 for record in records if record.extractor_category),
    }


def download_artifact_summary(download_dirs: Iterable[Path], *, cleanup: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    allowed_roots = [resolve_repo_path(path) for path in download_dirs]
    for root in allowed_roots:
        require_under_path(root, ROOT / ".local_manifests", code="download_path_outside_local_manifests")
        if "phase-4.4p2r-f1-gallery-dl-json-import" not in root.as_posix():
            raise OutputPathError("download_path_not_phase_specific")

    image_files: list[Path] = []
    total_bytes = 0
    for root in allowed_roots:
        if not root.exists():
            continue
        for file_path in root.rglob("*"):
            if file_path.is_file() and file_path.suffix.lower() in IMAGE_EXTENSIONS:
                require_under_path(file_path, root, code="download_file_outside_allowed_root")
                image_files.append(file_path)
                total_bytes += file_path.stat().st_size

    cleanup_count = 0
    cleanup_bytes = 0
    if cleanup:
        for file_path in image_files:
            size = file_path.stat().st_size
            file_path.unlink()
            cleanup_count += 1
            cleanup_bytes += size

    public = {
        "bounded_downloads_used": bool(image_files),
        "downloaded_file_count": len(image_files),
        "downloaded_total_bytes": total_bytes,
        "cleanup_performed": cleanup,
        "cleanup_file_count": cleanup_count,
        "cleanup_total_bytes": cleanup_bytes,
        "download_roots_are_phase_specific": True,
    }
    private = {
        **public,
        "download_files_private": [_rel(path) for path in image_files],
    }
    return public, private


def detect_unexpected_images_under_phase(
    phase_roots: Iterable[Path],
    *,
    allowed_download_dirs: Iterable[Path],
) -> dict[str, Any]:
    allowed = [resolve_repo_path(path) for path in allowed_download_dirs]
    unexpected: list[str] = []
    for root_value in phase_roots:
        root = resolve_repo_path(root_value)
        if not root.exists():
            continue
        require_under_path(root, ROOT / ".local_manifests", code="phase_output_path_outside_local_manifests")
        for file_path in root.rglob("*"):
            if not file_path.is_file() or file_path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            if any(is_under_path(file_path, allowed_root) for allowed_root in allowed):
                continue
            unexpected.append(_rel(file_path))
    return {
        "unexpected_image_files_detected": bool(unexpected),
        "unexpected_image_file_count": len(unexpected),
        "unexpected_image_files_private": unexpected,
    }


def copy_inputs_to_private_raw(files: Iterable[Path], raw_dir: Path) -> list[str]:
    raw_dir = resolve_repo_path(raw_dir)
    require_under_path(raw_dir, ROOT / ".local_manifests", code="raw_output_path_violation")
    raw_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for index, file_path in enumerate(files, start=1):
        destination = raw_dir / f"input-{index:02d}{file_path.suffix.lower()}"
        if file_path.resolve() == destination.resolve():
            copied.append(_rel(destination))
            continue
        shutil.copy2(file_path, destination)
        copied.append(_rel(destination))
    return copied


def build_correspondence_summary(records: Sequence[PixivGalleryDlMetadataRecord], download_public: Mapping[str, Any]) -> dict[str, Any]:
    statuses: Counter[str] = Counter()
    for record in records:
        if record.page_index_status == "missing_page_index":
            statuses["page_index_metadata_missing"] += 1
        elif record.page_index_status == "page_index_out_of_range":
            statuses["page_index_out_of_range"] += 1
        elif download_public.get("downloaded_file_count", 0):
            statuses["visual_reference_available_not_checked"] += 1
        else:
            statuses["metadata_work_page_match_no_visual_check"] += 1
    return {
        "visual_check_performed": False,
        "status_counts": dict(sorted(statuses.items())),
        "image_correspondence_is_blocker": False,
    }


def external_adapter_readiness_design() -> dict[str, Any]:
    return {
        "command_boundary": [
            "Future V.I.O.L.E.T. may invoke user-installed gallery-dl in a separately approved phase.",
            "This stage proves bounded local JSON import and metadata/reference command shape only.",
        ],
        "config_boundary": [
            "gallery-dl config remains user-managed outside the repo.",
            "No token, cookie, refresh token, or authorization header is stored in V.I.O.L.E.T. DB or public reports.",
        ],
        "version_capture": "record gallery-dl --version or equivalent module entrypoint version",
        "output_contract": [
            "JSON/JSONL/NDJSON input lives under ignored .local_manifests for pilots.",
            "Parser accepts dict records, gallery-dl event arrays, JSON arrays, and JSONL.",
            "Public reports use aggregate-only redacted output.",
        ],
        "download_contract": [
            "metadata-first",
            "optional bounded reference download only under phase-specific .local_manifests paths",
            "no broad original download",
            "file and byte counts must be reported",
            "cleanup option must not delete outside phase-specific paths",
        ],
        "safety_gates": [
            "sample size 5 by default and 10 maximum without renewed approval",
            "no original image download by default",
            "request/command count logging",
            "timeout/retry policy before broad use",
            "no broad run without a provider run ledger",
        ],
        "failure_handling": [
            "missing executable",
            "gallery-dl config/auth blocked",
            "malformed JSON",
            "missing work_id",
            "missing page_index",
            "ambiguous local match",
            "secret-like payload detected",
            "unexpected downloaded image file detected",
            "output path violation",
        ],
    }


def future_route_recommendation(records: Sequence[PixivGalleryDlMetadataRecord], join_summary: Mapping[str, Any]) -> dict[str, Any]:
    richness_counts = Counter(record.metadata_richness for record in records)
    join_counts = Counter(join_summary.get("status_counts", {}))
    local_match_count = join_counts.get("metadata_matches_local_filename_prior", 0)
    rich_count = richness_counts.get("rich_structured_metadata", 0) + richness_counts.get("partial_structured_metadata", 0)
    if records and rich_count and local_match_count:
        return {
            "decision": "A_proceed_to_external_gallery_dl_metadata_reference_adapter_pilot",
            "reason": "gallery-dl JSON provided structured metadata and at least one local filename-prior join succeeded",
            "db_persistence": "later_phase_only",
        }
    if records and rich_count:
        return {
            "decision": "B_do_another_local_json_export_with_better_gallery_dl_options_or_local_prior_ids",
            "reason": "metadata is structured, but local source-prior join did not prove the next adapter boundary yet",
            "db_persistence": "not_recommended_in_this_stage",
        }
    if records:
        return {
            "decision": "C_consider_internal_pixivpy_style_adapter_only_if_gallery_dl_shape_cannot_be_improved",
            "reason": "records were parseable but metadata richness was insufficient",
            "db_persistence": "not_recommended",
        }
    return {
        "decision": "D_stop_pixiv_route_for_now",
        "reason": "no usable gallery-dl JSON metadata records were imported",
        "db_persistence": "not_recommended",
    }


def build_public_summary(
    *,
    generated_at: str,
    parse_result: ParseResult,
    records: Sequence[PixivGalleryDlMetadataRecord],
    gallery_env: Mapping[str, Any],
    command_summary: Mapping[str, Any],
    join_summary: Mapping[str, Any],
    db_identity: Mapping[str, Any] | None,
    download_public: Mapping[str, Any],
    unexpected_images: Mapping[str, Any],
    pr_context: Mapping[str, Any],
) -> dict[str, Any]:
    richness_counts = Counter(record.metadata_richness for record in records)
    extractor_counts = Counter(record.extractor_category or "missing" for record in records)
    record_shape_counts = Counter(record.record_shape for record in records)
    source_file_count = len(parse_result.files)
    public = {
        "phase": PHASE,
        "title": TITLE,
        "generated_at": generated_at,
        "artifact_lifecycle": "phase_scoped_operational_runner",
        "why_this_stage_exists": (
            "PR #87 chose gallery-dl JSON metadata import as the immediate Pixiv metadata route "
            "after PR #86 public-page preview probing proved unsuitable as a durable metadata foundation."
        ),
        "pr_context": dict(pr_context),
        "gallery_dl_environment": dict(gallery_env),
        "command_summary": {
            "metadata_first_command_template": GALLERY_DL_METADATA_COMMAND_TEMPLATE,
            "module_entrypoint_template_used_when_bare_command_missing": GALLERY_DL_MODULE_COMMAND_TEMPLATE,
            **dict(command_summary),
        },
        "input_summary": {
            "json_input_found": source_file_count > 0,
            "input_file_count": source_file_count,
            "record_count": len(records),
            "invalid_json_count": parse_result.invalid_json_count,
            "skipped_invalid_count": parse_result.skipped_invalid_count,
            "unsupported_shape_count": parse_result.unsupported_shape_count,
        },
        "schema_field_availability": field_availability(records),
        "metadata_richness_distribution": dict(sorted(richness_counts.items())),
        "record_shape_distribution": dict(sorted(record_shape_counts.items())),
        "extractor_category_distribution": dict(sorted(extractor_counts.items())),
        "local_source_prior_join": dict(join_summary),
        "page_index_validation_results": dict(join_summary.get("page_index_status_counts", {})),
        "tags_artist_title_page_count_availability": {
            key: field_availability(records)[key]
            for key in ("tags", "translated_tags", "artist_name", "artist_id", "title", "page_count")
        },
        "normalized_dto": {
            "name": "PixivGalleryDlMetadataRecord",
            "source_adapter": "gallery_dl_json",
            "db_write_allowed": False,
            "privacy_level": "private_exact_mapping",
            "public_projection_sample": records[0].public_projection() if records else None,
        },
        "download_summary": dict(download_public),
        "unexpected_image_scan": {
            "unexpected_image_files_detected": unexpected_images.get("unexpected_image_files_detected", False),
            "unexpected_image_file_count": unexpected_images.get("unexpected_image_file_count", 0),
        },
        "correspondence_feasibility": build_correspondence_summary(records, download_public),
        "external_adapter_readiness_design": external_adapter_readiness_design(),
        "future_route_recommendation": future_route_recommendation(records, join_summary),
        "db_identity": dict(db_identity or {"db_read": False}),
        "privacy_and_safety_confirmation": {
            "public_report_contains_exact_pixiv_ids": False,
            "public_report_contains_exact_local_filenames": False,
            "public_report_contains_exact_media_id_mapping": False,
            "public_report_contains_raw_gallery_dl_json": False,
            "public_report_contains_raw_image_urls": False,
            "sensitive_material_printed_or_committed": False,
            "db_write": False,
            "db_migration": False,
            "provider_cache_write": False,
            "entity_evidence_write": False,
            "media_entity_candidate_write": False,
            "local_source_hint_write": False,
            "confirmed_assignment": False,
            "automatic_entity": False,
            "source_or_icloud_mutation": False,
            "app_managed_storage_mutation": False,
            "downloaded_artifacts_committed": False,
            "push_main": False,
            "merge": False,
        },
    }
    assert_public_payload_safe(public, private_markers=_private_markers(records))
    return public


def build_markdown_report(summary: Mapping[str, Any], *, private_markers: Iterable[str] = ()) -> str:
    lines = [
        f"# Phase {PHASE} - {TITLE}",
        "",
        "## Why This Stage Exists",
        "",
        str(summary["why_this_stage_exists"]),
        "",
        "## PR #87 Merge Confirmation",
        "",
        f"- PR #87 state: `{summary['pr_context'].get('pr87_state')}`.",
        f"- PR #87 merged at: `{summary['pr_context'].get('pr87_merged_at')}`.",
        f"- PR #87 merge commit: `{summary['pr_context'].get('pr87_merge_commit')}`.",
        f"- PR #86 state: `{summary['pr_context'].get('pr86_state')}`; treated as superseded diagnostic evidence only.",
        "",
        "## gallery-dl Environment",
        "",
        f"- Available: `{summary['gallery_dl_environment'].get('gallery_dl_available')}`.",
        f"- Version: `{summary['gallery_dl_environment'].get('gallery_dl_version')}`.",
        f"- Command entrypoint used by this Codex shell: `{summary['gallery_dl_environment'].get('command_entrypoint')}`.",
        f"- Bare `gallery-dl` on PATH: `{summary['gallery_dl_environment'].get('bare_gallery_dl_on_path')}`.",
        "",
        "## Command Summary",
        "",
        f"- Metadata-first template: `{summary['command_summary']['metadata_first_command_template']}`.",
        f"- Module fallback template: `{summary['command_summary']['module_entrypoint_template_used_when_bare_command_missing']}`.",
        f"- Metadata command count: `{summary['command_summary'].get('metadata_command_count')}`.",
        f"- Metadata success count: `{summary['command_summary'].get('metadata_success_count')}`.",
        f"- Metadata failure count: `{summary['command_summary'].get('metadata_failure_count')}`.",
        f"- Bounded downloads used: `{summary['download_summary'].get('bounded_downloads_used')}`.",
        f"- Downloaded file count: `{summary['download_summary'].get('downloaded_file_count')}`.",
        f"- Downloaded total bytes: `{summary['download_summary'].get('downloaded_total_bytes')}`.",
        f"- Cleanup performed: `{summary['download_summary'].get('cleanup_performed')}`.",
        "",
        "## Input And Records",
        "",
        f"- Input file count: `{summary['input_summary']['input_file_count']}`.",
        f"- Record count: `{summary['input_summary']['record_count']}`.",
        f"- Invalid JSON count: `{summary['input_summary']['invalid_json_count']}`.",
        f"- Unsupported shape count: `{summary['input_summary']['unsupported_shape_count']}`.",
        f"- Record shape distribution: `{json.dumps(summary['record_shape_distribution'], sort_keys=True)}`.",
        "",
        "## Schema Field Availability",
        "",
        f"- Field availability: `{json.dumps(summary['schema_field_availability'], sort_keys=True)}`.",
        f"- Tags/artist/title/page_count: `{json.dumps(summary['tags_artist_title_page_count_availability'], sort_keys=True)}`.",
        f"- Metadata richness distribution: `{json.dumps(summary['metadata_richness_distribution'], sort_keys=True)}`.",
        "",
        "## Local Source-Prior Join",
        "",
        f"- Join status counts: `{json.dumps(summary['local_source_prior_join'].get('status_counts', {}), sort_keys=True)}`.",
        f"- Local prior keys without metadata: `{summary['local_source_prior_join'].get('local_prior_without_metadata')}`.",
        f"- Local prior total media inspected: `{summary['local_source_prior_join'].get('local_prior_total_media_inspected')}`.",
        "",
        "## Page Index Validation",
        "",
        f"- Page-index status counts: `{json.dumps(summary['page_index_validation_results'], sort_keys=True)}`.",
        "",
        "## Normalized DTO",
        "",
        f"- DTO name: `{summary['normalized_dto']['name']}`.",
        f"- Source adapter: `{summary['normalized_dto']['source_adapter']}`.",
        f"- DB write allowed: `{summary['normalized_dto']['db_write_allowed']}`.",
        f"- Privacy level: `{summary['normalized_dto']['privacy_level']}`.",
        "",
        "## Correspondence Feasibility",
        "",
        f"- Visual check performed: `{summary['correspondence_feasibility']['visual_check_performed']}`.",
        f"- Status counts: `{json.dumps(summary['correspondence_feasibility']['status_counts'], sort_keys=True)}`.",
        f"- Image correspondence blocker: `{summary['correspondence_feasibility']['image_correspondence_is_blocker']}`.",
        "",
        "## External Adapter Readiness",
        "",
    ]
    for key, value in summary["external_adapter_readiness_design"].items():
        lines.append(f"- `{key}`: `{json.dumps(value, ensure_ascii=False, sort_keys=True)}`.")
    lines.extend(
        [
            "",
            "## Future Route Recommendation",
            "",
            f"- Decision: `{summary['future_route_recommendation']['decision']}`.",
            f"- Reason: `{summary['future_route_recommendation']['reason']}`.",
            f"- DB persistence: `{summary['future_route_recommendation']['db_persistence']}`.",
            "",
            "## Privacy And Safety Confirmation",
            "",
        ]
    )
    for key, value in summary["privacy_and_safety_confirmation"].items():
        lines.append(f"- `{key}`: `{value}`.")
    lines.append("")
    report = "\n".join(lines)
    assert_public_payload_safe(report, private_markers=private_markers)
    return report


def build_private_sheet_csv(records: Sequence[PixivGalleryDlMetadataRecord]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "work_id",
            "page_index",
            "page_count",
            "title",
            "artist_name",
            "artist_id",
            "tag_count",
            "translated_tag_count",
            "metadata_richness",
            "local_match_status",
            "page_index_status",
            "local_media_id_private",
            "duplicate_local_media_ids_private",
            "canonical_url",
            "gallery_dl_filename",
        ],
        lineterminator="\n",
    )
    writer.writeheader()
    for record in records:
        writer.writerow(
            {
                "work_id": record.work_id or "",
                "page_index": "" if record.page_index is None else record.page_index,
                "page_count": "" if record.page_count is None else record.page_count,
                "title": record.title or "",
                "artist_name": record.artist_name or "",
                "artist_id": record.artist_id or "",
                "tag_count": len(record.tags),
                "translated_tag_count": len(record.translated_tags),
                "metadata_richness": record.metadata_richness,
                "local_match_status": record.local_match_status,
                "page_index_status": record.page_index_status,
                "local_media_id_private": record.local_media_id_private or "",
                "duplicate_local_media_ids_private": ",".join(str(item) for item in record.duplicate_local_media_ids_private),
                "canonical_url": record.canonical_url or "",
                "gallery_dl_filename": record.gallery_dl_filename or "",
            }
        )
    return buffer.getvalue()


def build_private_sheet_markdown(records: Sequence[PixivGalleryDlMetadataRecord]) -> str:
    lines = [
        f"# Phase {PHASE} Private gallery-dl Import Sheet",
        "",
        "This ignored local artifact may contain exact Pixiv IDs, filenames, and media IDs.",
        "",
        "| work_id | page_index | page_count | title | artist | tags | richness | local_match_status | local_media_id |",
        "|---|---:|---:|---|---|---:|---|---|---:|",
    ]
    for record in records:
        lines.append(
            "| {work_id} | {page_index} | {page_count} | {title} | {artist} | {tags} | {richness} | {match} | {media_id} |".format(
                work_id=record.work_id or "",
                page_index="" if record.page_index is None else record.page_index,
                page_count="" if record.page_count is None else record.page_count,
                title=(record.title or "").replace("|", "\\|"),
                artist=(record.artist_name or "").replace("|", "\\|"),
                tags=len(record.tags),
                richness=record.metadata_richness,
                match=record.local_match_status,
                media_id="" if record.local_media_id_private is None else record.local_media_id_private,
            )
        )
    lines.append("")
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="JSON/JSONL/NDJSON file or directory")
    parser.add_argument("--skip-invalid", action="store_true")
    parser.add_argument("--no-db", action="store_true", help="Skip read-only local prior DB join")
    parser.add_argument("--gallery-dl-version", default=None)
    parser.add_argument("--metadata-command-count", type=int, default=0)
    parser.add_argument("--metadata-success-count", type=int, default=0)
    parser.add_argument("--metadata-failure-count", type=int, default=0)
    parser.add_argument("--reference-command-count", type=int, default=0)
    parser.add_argument("--cleanup-downloads", action="store_true")
    parser.add_argument("--report-md", default=str(REPORT_MD))
    parser.add_argument("--report-json", default=str(REPORT_JSON))
    parser.add_argument("--details-json", default=str(PRIVATE_DETAILS_JSON))
    parser.add_argument("--sheet-csv", default=str(PRIVATE_SHEET_CSV))
    parser.add_argument("--sheet-md", default=str(PRIVATE_SHEET_MD))
    parser.add_argument("--raw-dir", default=str(PRIVATE_RAW_DIR))
    parser.add_argument("--download-dir", default=str(DOWNLOAD_DIR))
    parser.add_argument("--pilot-download-dir", default=str(PILOT_DOWNLOAD_DIR))
    parser.add_argument("--pr87-state", default="MERGED")
    parser.add_argument("--pr87-merged-at", default="2026-06-01T04:05:37Z")
    parser.add_argument("--pr87-merge-commit", default="d74f8e073c27dec70fd4f6e5df192eb90450c458")
    parser.add_argument("--pr87-url", default="https://github.com/kyloris0660/VIOLET/pull/87")
    parser.add_argument("--pr86-state", default="CLOSED")
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    parse_result = parse_gallery_dl_json_inputs(args.input, skip_invalid=args.skip_invalid)
    gallery_env = detect_gallery_dl_environment()
    adapter_version = args.gallery_dl_version or gallery_env.get("gallery_dl_version")
    records = normalize_records(parse_result, adapter_version=adapter_version)

    db_identity: dict[str, Any] | None = None
    prior_index: LocalPriorIndex | None = None
    if not args.no_db:
        config = load_project_config()
        engine = create_engine(config.database_url)
        install_read_only_guard(engine)
        SessionLocal = sessionmaker(bind=engine)
        session = SessionLocal()
        try:
            db_identity = prove_db_identity(session, config)
            prior_index = build_local_prior_index(session)
        finally:
            session.close()
            engine.dispose()

    records, join_summary = join_records_to_local_priors(records, prior_index)
    download_public, download_private = download_artifact_summary(
        [resolve_repo_path(args.download_dir), resolve_repo_path(args.pilot_download_dir)],
        cleanup=args.cleanup_downloads,
    )
    unexpected_images = detect_unexpected_images_under_phase(
        [PHASE_OUTPUT_DIR, DOWNLOAD_DIR, PILOT_DOWNLOAD_DIR],
        allowed_download_dirs=[resolve_repo_path(args.download_dir), resolve_repo_path(args.pilot_download_dir)],
    )
    if unexpected_images["unexpected_image_files_detected"]:
        raise OutputPathError("gallery_dl_unexpected_download_volume")

    private_raw_copies = copy_inputs_to_private_raw(parse_result.files, resolve_repo_path(args.raw_dir))
    command_summary = {
        "metadata_command_count": args.metadata_command_count,
        "metadata_success_count": args.metadata_success_count,
        "metadata_failure_count": args.metadata_failure_count,
        "reference_command_count": args.reference_command_count,
        "reference_command_template": None,
        "exact_commands_private_only": True,
    }
    pr_context = {
        "pr87_state": args.pr87_state,
        "pr87_merged_at": args.pr87_merged_at,
        "pr87_merge_commit": args.pr87_merge_commit,
        "pr87_url": args.pr87_url,
        "pr86_state": args.pr86_state,
        "pr86_modified": False,
    }
    public_summary = build_public_summary(
        generated_at=_now_iso(),
        parse_result=parse_result,
        records=records,
        gallery_env=gallery_env,
        command_summary=command_summary,
        join_summary=join_summary,
        db_identity=db_identity,
        download_public=download_public,
        unexpected_images=unexpected_images,
        pr_context=pr_context,
    )
    markers = _private_markers(records)
    public_report = build_markdown_report(public_summary, private_markers=markers)
    private_details = {
        "phase": PHASE,
        "generated_at": public_summary["generated_at"],
        "contains_exact_pixiv_ids": True,
        "contains_exact_media_ids": True,
        "contains_exact_local_filenames_or_basenames": True,
        "public_report_contains_exact_mappings": False,
        "records": [record.to_private_dict() for record in records],
        "parse_files_private": [_rel(path) for path in parse_result.files],
        "private_raw_copies": private_raw_copies,
        "download_artifacts": download_private,
        "unexpected_image_scan_private": unexpected_images,
        "join_summary_public": join_summary,
        "db_write_allowed": False,
    }
    assert_no_secret_like_payload(private_details)

    write_json(resolve_repo_path(args.report_json), public_summary, expected_parent=Path("docs/reports"))
    write_text(resolve_repo_path(args.report_md), public_report, expected_parent=Path("docs/reports"))
    write_json(resolve_repo_path(args.details_json), private_details, expected_parent=Path(".local_manifests"))
    write_text(resolve_repo_path(args.sheet_csv), build_private_sheet_csv(records), expected_parent=Path(".local_manifests"))
    write_text(resolve_repo_path(args.sheet_md), build_private_sheet_markdown(records), expected_parent=Path(".local_manifests"))
    return {
        "success": True,
        "phase": PHASE,
        "report_md": _rel(resolve_repo_path(args.report_md)),
        "report_json": _rel(resolve_repo_path(args.report_json)),
        "private_details_json": _rel(resolve_repo_path(args.details_json)),
        "private_sheet_csv": _rel(resolve_repo_path(args.sheet_csv)),
        "private_sheet_md": _rel(resolve_repo_path(args.sheet_md)),
        "record_count": len(records),
        "metadata_richness_distribution": public_summary["metadata_richness_distribution"],
        "local_join_status_counts": public_summary["local_source_prior_join"]["status_counts"],
        "downloaded_file_count": public_summary["download_summary"]["downloaded_file_count"],
        "downloaded_total_bytes": public_summary["download_summary"]["downloaded_total_bytes"],
        "future_route_recommendation": public_summary["future_route_recommendation"]["decision"],
    }


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        result = run(args)
    except Phase44P2RF1Error as exc:
        print(json.dumps({"success": False, "phase": PHASE, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
