"""Phase 4.4-B0 sample-gated reverse-search preflight runner.

Lifecycle: phase-scoped operational runner. This prepares only a redacted
request plan and reports; it does not call providers, upload bytes, or write
runtime DB/entity/cache rows.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable

from PIL import Image
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.enums import ContentClassEnum, FileTypeEnum  # noqa: E402
from app.models import Media  # noqa: E402
from app.services.entity_metadata_service import hash_provider_query  # noqa: E402


APPROVED_SAMPLE_IDS = (2690, 2687, 2670, 2654, 2647)
APPROVED_SAMPLE_SET = frozenset(APPROVED_SAMPLE_IDS)
DEFAULT_PROVIDER = "saucenao"
DEFAULT_PROVIDER_CATEGORY = "saucenao_style_reverse_search"
DEFAULT_QUERY_TYPE = "reverse_search_derived_image"
DEFAULT_INPUT_KIND = "derived_resized_image_plan"
INPUT_PRIVACY_MODE = "local_preflight_only_no_upload"
TRANSFORM_POLICY_VERSION = "phase44b0-derived-resized-stripped-plan-v1"
PUBLIC_REPORT_DIR = Path("docs/reports")
LOCAL_DETAILS_DIR = Path(".local_manifests")
BLOCKED_WRITE_TABLES = (
    "ProviderCache",
    "NegativeLookupCache",
    "EntityEvidence",
    "MediaEntityCandidate",
    "MediaEntityAssignment",
)


class Phase44B0Error(RuntimeError):
    pass


class SampleGateError(Phase44B0Error):
    pass


class EnvBlockedError(Phase44B0Error):
    pass


class IdentityBlockedError(Phase44B0Error):
    pass


class OutputPathError(Phase44B0Error):
    pass


class ReadOnlyViolation(Phase44B0Error):
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


def _enum_label(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


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
    violet_env = _env_value(dotenv_values, "VIOLET_ENV", "development").strip().lower() or "development"
    if violet_env == "test":
        raise EnvBlockedError("env_blocked: VIOLET_ENV=test is not allowed for Phase 4.4-B0")
    if _env_value(dotenv_values, "TEST_DATABASE_URL", "").strip():
        raise EnvBlockedError("env_blocked: TEST_DATABASE_URL is set; refusing development sample preflight")

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


def _public_path_label(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
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


def parse_media_ids(media_ids: Iterable[int] | None, *, allow_subset: bool = False) -> list[int]:
    values = list(media_ids or [])
    if not values:
        raise SampleGateError("sample_gate_blocked: --media-ids is required and must be explicit")
    requested = list(dict.fromkeys(int(item) for item in values))
    outside = [item for item in requested if item not in APPROVED_SAMPLE_SET]
    if outside:
        raise SampleGateError(
            "sample_gate_blocked: media IDs outside the approved Phase 4.4-B0 sample set: "
            + ", ".join(str(item) for item in outside)
        )
    omitted = [item for item in APPROVED_SAMPLE_IDS if item not in requested]
    if omitted and not allow_subset:
        raise SampleGateError(
            "sample_gate_blocked: subset requires --allow-subset; omitted approved IDs: "
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


def inspect_storage_path(storage_root: Path, stored_path: str | None, *, expected_kind: str, read_dimensions: bool = False) -> StorageCheck:
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


def budget_plan(eligible_count: int) -> dict[str, Any]:
    count = min(int(eligible_count), len(APPROVED_SAMPLE_IDS))
    return {
        "provider_category": DEFAULT_PROVIDER_CATEGORY,
        "max_items": count,
        "max_requests": count,
        "requests_per_minute": 10,
        "concurrency": 1,
        "timeout": "provider_policy_placeholder",
        "max_failures": 2,
        "max_consecutive_failures": 2,
        "max_same_reason_failures": 2,
        "max_runtime": "10 minutes",
        "live_requests_allowed": False,
        "stop_conditions": [
            "auth_failed",
            "forbidden",
            "schema_changed",
            "privacy_leak",
            "rate_limit_exceeded",
            "unexpected_mutation",
            "redaction_failure",
            "user_abort",
        ],
    }


def future_write_mapping() -> dict[str, Any]:
    return {
        "b0_runtime_writes_allowed": False,
        "future_live_pilot_only_after_explicit_approval": {
            "ProviderCache": "redacted normalized provider response cache",
            "NegativeLookupCache": "privacy/no-match/low-confidence negative cache",
            "EntityEvidence": "redacted reverse_search evidence",
            "MediaEntityCandidate": "optional suggested candidate after confidence policy approval",
        },
        "explicitly_not_mapped_in_first_live_pilot": {
            "MediaEntityAssignment": "no confirmed assignments in the first live pilot",
            "Entity": "no automatic trusted entity creation",
        },
        "b0_blocked_tables": list(BLOCKED_WRITE_TABLES),
    }


def make_request_plan_row(
    *,
    media_id: int,
    content_class: str,
    eligibility_status: str,
    blocked_reason: str | None,
    provider_key: str,
    input_kind: str,
) -> dict[str, Any]:
    request_shape = {
        "phase": "4.4-B0",
        "provider_key": provider_key,
        "provider_category": DEFAULT_PROVIDER_CATEGORY,
        "query_type": DEFAULT_QUERY_TYPE,
        "input_kind": input_kind,
        "input_privacy_mode": INPUT_PRIVACY_MODE,
        "media_ref": f"approved_media_id:{media_id}",
        "content_class": content_class,
        "transform_policy_version": TRANSFORM_POLICY_VERSION,
        "upload_allowed": False,
        "live_request_allowed": False,
    }
    query_hash = hash_provider_query(request_shape)
    return {
        "media_id": int(media_id),
        "content_class": content_class,
        "eligibility_status": eligibility_status,
        "blocked_reason": blocked_reason,
        "provider_key": provider_key,
        "provider_category": DEFAULT_PROVIDER_CATEGORY,
        "query_type": DEFAULT_QUERY_TYPE,
        "input_kind": input_kind,
        "input_privacy_mode": INPUT_PRIVACY_MODE,
        "would_send_original": False,
        "would_send_thumbnail": False,
        "would_send_derived_image": False,
        "live_request_allowed": False,
        "local_path_included": False,
        "filename_included": False,
        "source_label_included": False,
        "request_shape_redacted": request_shape,
        "query_hash_plan": query_hash,
        "cache_key_plan": f"{provider_key}:{DEFAULT_QUERY_TYPE}:{query_hash}",
        "future_write_plan": {
            "b0_write_allowed": False,
            "ProviderCache": "future_only_after_live_pilot_approval",
            "NegativeLookupCache": "future_only_after_live_pilot_approval",
            "EntityEvidence": "future_only_after_live_pilot_approval",
            "MediaEntityCandidate": "future_only_after_live_pilot_approval",
            "MediaEntityAssignment": "not_mapped_for_first_live_pilot",
        },
    }


def _media_debug_details(media: Media | None, original: StorageCheck | None, thumbnail: StorageCheck | None) -> dict[str, Any]:
    if media is None:
        return {"media_id": None, "found": False, "original_status": "not_checked", "thumbnail_status": "not_checked"}
    return {
        "media_id": int(media.id),
        "found": True,
        "file_type": _file_type_label(media.file_type),
        "mime_type_present": bool(media.mime_type),
        "db_dimensions_present": bool(media.width and media.height),
        "original_status": original.status if original else "not_checked",
        "original_size_bytes": original.size_bytes if original else None,
        "thumbnail_status": thumbnail.status if thumbnail else "not_checked",
        "thumbnail_size_bytes": thumbnail.size_bytes if thumbnail else None,
        "thumbnail_dimensions": thumbnail.dimensions if thumbnail else None,
        "source_field_read_for_fallback": False,
    }


def build_preflight(
    db: Session,
    *,
    media_ids: list[int],
    storage_root: Path,
    provider_key: str = DEFAULT_PROVIDER,
    input_kind: str = DEFAULT_INPUT_KIND,
    identity: dict[str, Any] | None = None,
    no_active_server_preflight: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = db.query(Media).filter(Media.id.in_(media_ids)).all()
    by_id = {int(row.id): row for row in rows}
    request_plan: list[dict[str, Any]] = []
    details_rows: list[dict[str, Any]] = []
    content_distribution: Counter[str] = Counter()
    blocked_reasons: Counter[str] = Counter()
    derived_status: Counter[str] = Counter()

    for media_id in media_ids:
        media = by_id.get(media_id)
        if media is None:
            row = make_request_plan_row(
                media_id=media_id,
                content_class="missing",
                eligibility_status="blocked",
                blocked_reason="missing_media",
                provider_key=provider_key,
                input_kind=input_kind,
            )
            request_plan.append(row)
            content_distribution["missing"] += 1
            blocked_reasons["missing_media"] += 1
            derived_status["blocked_missing_media"] += 1
            details_rows.append({"media_id": media_id, "found": False, "blocked_reason": "missing_media"})
            continue

        content_class = _content_class_label(media.content_class)
        original = inspect_storage_path(storage_root, media.path, expected_kind="app_managed_original")
        thumbnail = inspect_storage_path(storage_root, media.thumbnail_path, expected_kind="app_managed_thumbnail", read_dimensions=True)
        reason = _blocked_reason_for_media(media, original, thumbnail)
        status = "blocked" if reason else "eligible"
        if reason:
            blocked_reasons[reason] += 1
            if reason.startswith("blocked_by_content_class"):
                derived_status["blocked_content_class"] += 1
            elif reason.startswith("blocked_by_original"):
                derived_status["blocked_original_unavailable"] += 1
            elif reason.startswith("blocked_by_thumbnail"):
                derived_status["blocked_thumbnail_unavailable"] += 1
            else:
                derived_status["blocked_other"] += 1
        else:
            derived_status["ready_plan_from_app_thumbnail"] += 1
        content_distribution[content_class] += 1
        request_plan.append(
            make_request_plan_row(
                media_id=media_id,
                content_class=content_class,
                eligibility_status=status,
                blocked_reason=reason,
                provider_key=provider_key,
                input_kind=input_kind,
            )
        )
        detail = _media_debug_details(media, original, thumbnail)
        detail["blocked_reason"] = reason
        detail["eligibility_status"] = status
        details_rows.append(detail)

    eligible_count = sum(1 for row in request_plan if row["eligibility_status"] == "eligible")
    blocked_count = len(request_plan) - eligible_count
    summary = {
        "phase": "4.4-B0",
        "lifecycle": "phase-scoped operational runner",
        "generated_at": _now_iso(),
        "approved_sample_media_ids": list(APPROVED_SAMPLE_IDS),
        "requested_media_ids": media_ids,
        "no_active_server_preflight": no_active_server_preflight or {"result": "not_recorded"},
        "identity": identity or {},
        "counts": {
            "approved_sample_count": len(APPROVED_SAMPLE_IDS),
            "requested_count": len(media_ids),
            "found_media_count": len(by_id),
            "eligible_count": eligible_count,
            "blocked_count": blocked_count,
            "blocked_count_by_reason": dict(sorted(blocked_reasons.items())),
            "content_class_distribution": dict(sorted(content_distribution.items())),
        },
        "input_policy": {
            "input_kind": input_kind,
            "original_upload": False,
            "thumbnail_upload": False,
            "derived_upload": False,
            "live_request": False,
            "upload_allowed": False,
            "derived_files_generated": 0,
            "local_preflight_source": "app_managed_thumbnail_when_present",
        },
        "derived_input_preflight": {
            "status_counts": dict(sorted(derived_status.items())),
            "derived_files_generated": 0,
            "upload_attempted": False,
        },
        "redaction_proof": {
            "local_paths_included": False,
            "filenames_included": False,
            "source_labels_included": False,
            "raw_image_bytes_included": False,
            "secrets_included": False,
            "request_payloads_are_redacted_plans": True,
        },
        "request_budget": budget_plan(eligible_count),
        "provider_policy_stub": {
            "provider_key": provider_key,
            "provider_category": DEFAULT_PROVIDER_CATEGORY,
            "enabled": False,
            "official_api_terms_verified_for_live_run": False,
            "auth_mode": "none_in_B0",
            "live_requests_allowed": False,
        },
        "future_write_mapping": future_write_mapping(),
        "external_call_policy": {
            "external_provider_calls_attempted": False,
            "authenticated_provider_calls_attempted": False,
            "scraping_attempted": False,
            "reverse_search_execution_attempted": False,
            "upload_attempted": False,
        },
        "runtime_mutation_policy": {
            "db_writes_attempted": False,
            "provider_cache_writes_attempted": False,
            "entity_evidence_writes_attempted": False,
            "media_entity_candidate_writes_attempted": False,
            "media_entity_assignment_writes_attempted": False,
            "app_managed_storage_mutation_attempted": False,
            "source_icloud_mutation_attempted": False,
        },
        "live_pilot_readiness": {
            "ready_to_consider": eligible_count > 0,
            "still_requires_explicit_user_approval": True,
            "blocked_until_provider_policy_and_derived_input_approval": True,
        },
        "request_plan": request_plan,
    }
    details = {
        "phase": "4.4-B0",
        "generated_at": summary["generated_at"],
        "path_policy": {
            "absolute_paths_included": False,
            "source_icloud_paths_included": False,
            "filenames_included": False,
        },
        "sample_details": details_rows,
        "request_plan": request_plan,
    }
    return summary, details


WINDOWS_PATH_RE = re.compile(r"(?i)(^|[^A-Za-z0-9_])([A-Z]:[\\/])")
UNC_PATH_RE = re.compile(r"(^|[^A-Za-z0-9_])(\\\\|//)")
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
        raise Phase44B0Error(f"privacy_scan_failed: {all_issues}")


def build_markdown_report(summary: dict[str, Any]) -> str:
    counts = summary["counts"]
    blocked_lines = "\n".join(f"- `{k}`: `{v}`" for k, v in counts["blocked_count_by_reason"].items()) or "- none"
    class_lines = "\n".join(f"- `{k}`: `{v}`" for k, v in counts["content_class_distribution"].items()) or "- none"
    rows = "\n".join(
        f"| {r['media_id']} | {r['content_class']} | {r['eligibility_status']} | {r['blocked_reason'] or 'N/A'} | {r['input_kind']} | false | false | false |"
        for r in summary["request_plan"]
    )
    budget = summary["request_budget"]
    identity = summary["identity"]
    server = summary["no_active_server_preflight"]
    return f"""# Phase 4.4-B0 - Sample-gated Reverse-search Preflight

Date: {summary['generated_at']}

## Summary

Phase 4.4-B0 is a user-approved, sample-gated reverse-search preflight. It generated a local redacted request plan only. No provider calls, uploads, reverse-search execution, runtime DB writes, entity writes, classification, AI tagging, localization, staging copy, source/iCloud mutation, or app-managed storage mutation were performed.

## Approved Sample Gate

- Approved media IDs: `{', '.join(str(item) for item in summary['approved_sample_media_ids'])}`
- Requested media IDs: `{', '.join(str(item) for item in summary['requested_media_ids'])}`
- Approved sample count: `{counts['approved_sample_count']}`
- Found media count: `{counts['found_media_count']}`
- Eligible count: `{counts['eligible_count']}`
- Blocked count: `{counts['blocked_count']}`

## No-active-server Preflight

- Result: `{server.get('result', 'unknown')}`
- Listener backend: `{server.get('listener_backend', 'unknown')}`
- Occupied ports: `{server.get('occupied_count', 'unknown')}`
- Confirmed V.I.O.L.E.T. servers: `{server.get('confirmed_violet_count', 'unknown')}`
- Suspected V.I.O.L.E.T. servers: `{server.get('suspected_violet_count', 'unknown')}`

## DB / Storage Identity Proof

- `VIOLET_ENV`: `{identity.get('violet_env', 'unknown')}`
- Configured DB name: `{identity.get('configured_db_name', 'unknown')}`
- Actual DB name: `{identity.get('actual_db_name', 'unknown')}`
- DB identity result: `{identity.get('db_identity_result', 'unknown')}`
- Storage root mode: `{identity.get('storage_root_mode', 'unknown')}`
- Storage root explicit: `{identity.get('storage_root_explicitly_set', 'unknown')}`
- Storage root test-path check: `{identity.get('storage_root_test_path', 'unknown')}`
- Local paths redacted: `{identity.get('local_paths_redacted', True)}`

## Content-class Distribution

{class_lines}

## Blocked Count By Reason

{blocked_lines}

## Input Kind Policy

- Input kind: `{summary['input_policy']['input_kind']}`
- Original upload: `false`
- Thumbnail upload: `false`
- Derived image upload: `false`
- Live request: `false`
- Derived files generated: `0`

## Redacted Request Plan

| media_id | content_class | eligibility | blocked_reason | input_kind | send_original | send_thumbnail | send_derived |
|---:|---|---|---|---|---|---|---|
{rows}

## Redaction Proof

- Public report excludes local absolute paths, iCloud/source paths, filenames, source labels, raw image bytes, raw image hashes, raw provider payloads, and secrets.
- Request rows set `local_path_included=false`, `filename_included=false`, and `source_label_included=false`.

## Request Budget / Circuit Breaker Plan

- Provider category: `{budget['provider_category']}`
- Max items: `{budget['max_items']}`
- Max requests: `{budget['max_requests']}`
- Requests per minute: `{budget['requests_per_minute']}`
- Concurrency: `{budget['concurrency']}`
- Max failures: `{budget['max_failures']}`
- Max consecutive failures: `{budget['max_consecutive_failures']}`
- Max same-reason failures: `{budget['max_same_reason_failures']}`
- Max runtime: `{budget['max_runtime']}`
- Live requests allowed: `false`
- Stop conditions: `auth_failed`, `forbidden`, `schema_changed`, `privacy_leak`, `rate_limit_exceeded`, `unexpected_mutation`, `redaction_failure`, `user_abort`

## Provider Policy Stub

- Provider key: `{summary['provider_policy_stub']['provider_key']}`
- Provider category: `{summary['provider_policy_stub']['provider_category']}`
- Provider enabled: `false`
- Official API/TOS/rate-limit verification for live run: `false`
- Auth mode in B0: `none_in_B0`

## Future Write Mapping

- `ProviderCache`: future redacted normalized response cache only after explicit live-pilot approval.
- `NegativeLookupCache`: future privacy/no-match/low-confidence negative cache only after explicit live-pilot approval.
- `EntityEvidence`: future redacted reverse-search evidence only after explicit live-pilot approval.
- `MediaEntityCandidate`: future suggested candidates only after confidence policy approval.
- `MediaEntityAssignment`: not mapped for the first live pilot; confirmed assignments remain blocked.

## Safety Confirmation

- External provider calls: `0`
- Authenticated provider calls: `0`
- Scraping/crawler: `0`
- Reverse search execution: `0`
- Image/thumbnail/derived upload: `0`
- DB writes: `0`
- Provider/entity/candidate/assignment writes: `0`
- DB import/classification/AI tagging/localization/staging copy: `0`
- Entity Resolver/similarity/clustering: `0`
- Source/iCloud/app-managed storage mutation: `0`

## Live Pilot Readiness

- Ready to consider: `{summary['live_pilot_readiness']['ready_to_consider']}`
- Still requires explicit user approval for provider policy, official API/TOS/rate-limit verification, derived input generation, and any live request/upload.
- Phase 3.9 remains required before broad provider enrichment, repeated source-discovery scheduling, or larger-scale run ledger discipline.
"""


def install_read_only_guard(engine) -> None:
    blocked = ("INSERT", "UPDATE", "DELETE", "ALTER", "CREATE", "DROP", "TRUNCATE", "MERGE", "REPLACE", "COPY", "GRANT", "REVOKE", "COMMENT", "VACUUM")

    @event.listens_for(engine, "before_cursor_execute")
    def _block_writes(_conn, _cursor, statement, _parameters, _context, _executemany):
        if str(statement).lstrip().upper().startswith(blocked):
            raise ReadOnlyViolation("db_write_blocked: Phase 4.4-B0 runner is read-only")


def prove_db_identity(session: Session, config: ProjectConfig) -> dict[str, Any]:
    actual_db = session.execute(text("SELECT current_database()")).scalar()
    if str(actual_db) != "blombooru" or config.db_name != "blombooru":
        raise IdentityBlockedError(f"identity_blocked: expected DB blombooru, got {config.db_name!r}/{actual_db!r}")
    return {
        "violet_env": config.violet_env,
        "configured_db_name": config.db_name,
        "actual_db_name": str(actual_db),
        "db_identity_result": "development_blombooru_confirmed",
        "storage_root_mode": "explicit_storage_root" if config.storage_root_explicitly_set else "code_root_default",
        "storage_root_explicitly_set": config.storage_root_explicitly_set,
        "storage_root_test_path": _is_test_storage_path(config.storage_root),
        "storage_root_equals_code_root": config.storage_root.resolve() == config.project_root.resolve(),
        "local_paths_redacted": True,
    }


def _server_preflight_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "result": args.no_active_server_preflight_result,
        "listener_backend": args.no_active_server_listener_backend,
        "occupied_count": args.no_active_server_occupied_count,
        "confirmed_violet_count": args.no_active_server_confirmed_violet_count,
        "suspected_violet_count": args.no_active_server_suspected_violet_count,
    }


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


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media-ids", nargs="+", type=int, required=True)
    parser.add_argument("--allow-subset", action="store_true")
    parser.add_argument("--provider", default=DEFAULT_PROVIDER)
    parser.add_argument("--input-kind", default=DEFAULT_INPUT_KIND)
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--report-md", required=True)
    parser.add_argument("--local-details-json", required=True)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--no-active-server-preflight-result", default="clean")
    parser.add_argument("--no-active-server-listener-backend", default="windows_netstat")
    parser.add_argument("--no-active-server-occupied-count", type=int, default=0)
    parser.add_argument("--no-active-server-confirmed-violet-count", type=int, default=0)
    parser.add_argument("--no-active-server-suspected-violet-count", type=int, default=0)
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    media_ids = parse_media_ids(args.media_ids, allow_subset=args.allow_subset)
    report_json = resolve_output_path(args.report_json, expected_parent=PUBLIC_REPORT_DIR)
    report_md = resolve_output_path(args.report_md, expected_parent=PUBLIC_REPORT_DIR)
    local_details_json = resolve_output_path(args.local_details_json, expected_parent=LOCAL_DETAILS_DIR)
    config = load_project_config(ROOT)
    engine = create_engine(config.database_url, pool_pre_ping=True)
    install_read_only_guard(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    try:
        identity = prove_db_identity(session, config)
        summary, details = build_preflight(
            session,
            media_ids=media_ids,
            storage_root=config.storage_root,
            provider_key=args.provider,
            input_kind=args.input_kind,
            identity=identity,
            no_active_server_preflight=_server_preflight_from_args(args),
        )
        write_reports(summary, details, report_json=report_json, report_md=report_md, local_details_json=local_details_json)
        session.rollback()
        return summary
    finally:
        session.close()
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        summary = run(args)
    except Phase44B0Error as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"phase44b0_preflight_failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "phase": summary["phase"],
                "eligible_count": summary["counts"]["eligible_count"],
                "blocked_count": summary["counts"]["blocked_count"],
                "external_provider_calls_attempted": False,
                "db_writes_attempted": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
