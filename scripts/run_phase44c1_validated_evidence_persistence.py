"""Phase 4.4-C1 validated high-confidence evidence persistence runner.

Lifecycle: phase-scoped operational runner. It is intentionally narrow:
only the approved SauceNAO media IDs 2687 and 2670 are eligible for positive
ProviderCache, EntityEvidence, and suggestion-only MediaEntityCandidate writes.
It never calls a provider, uploads images, creates Entities, creates confirmed
assignments, mutates media tags, or runs localization.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models import (  # noqa: E402
    Entity,
    EntityEvidence,
    Media,
    MediaEntityAssignment,
    MediaEntityCandidate,
    ProviderCache,
    TagTranslation,
    blombooru_media_tags,
)
from app.services.provider_evidence_contract import (  # noqa: E402
    EvidencePersistencePlan,
    EvidenceStrength,
    LocalizationStatus,
    ManualValidationStatus,
    SourceMatchClass,
    assert_public_payload_safe,
)
from app.services.provider_evidence_persistence_service import (  # noqa: E402
    EvidencePersistenceOptions,
    persist_provider_evidence_plans,
    provider_cache_payload_ref,
)
from app.services.saucenao_evidence_mapper import map_saucenao_result_to_plan  # noqa: E402


PHASE = "4.4-C1"
PROVIDER = "saucenao"
QUERY_TYPE = "reverse_search_derived_image"
APPROVED_MEDIA_IDS = (2687, 2670)
APPROVED_RESULT_IDENTITIES = {
    2687: {
        "provider_key": PROVIDER,
        "result_id": "7695035",
        "source_host": "danbooru.donmai.us",
        "provider_index_label": "Danbooru",
        "post_url_path": "/posts/7695035",
    },
    2670: {
        "provider_key": PROVIDER,
        "result_id": "9366672",
        "source_host": "danbooru.donmai.us",
        "provider_index_label": "Danbooru",
        "post_url_path": "/posts/9366672",
    },
}
LOW_CONFIDENCE_EXCLUDED_IDS = (2690, 2654, 2647)
DEFAULT_LIVE_DETAILS = Path(".local_manifests/phase-4.4b1-live-rerun-details.json")
DEFAULT_METADATA_DETAILS = Path(".local_manifests/phase-4.4b1-metadata-extraction-audit-details.json")
DEFAULT_REPORT_MD = Path("docs/reports/phase-4.4c1-validated-evidence-persistence.md")
DEFAULT_REPORT_JSON = Path("docs/reports/phase-4.4c1-validated-evidence-persistence-summary.json")
DEFAULT_LOCAL_DETAILS = Path(".local_manifests/phase-4.4c1-validated-evidence-persistence-details.json")


class PhaseC1Error(RuntimeError):
    """Fail-closed runner error."""

    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise PhaseC1Error("blocked_missing_local_details", path.name)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PhaseC1Error("blocked_invalid_local_details_json", f"{path.name}:{exc}") from exc


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=json_default) + "\n", encoding="utf-8")


def write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def validate_local_artifact_flags(live_details: Mapping[str, Any], metadata_details: Mapping[str, Any]) -> None:
    checks = {
        "live_api_key_included": live_details.get("api_key_included") is False,
        "live_absolute_paths_included": live_details.get("absolute_paths_included") is False,
        "metadata_api_key_included": metadata_details.get("api_key_included") is False,
        "metadata_local_paths_included": metadata_details.get("local_paths_included") is False,
        "metadata_original_upload": metadata_details.get("original_upload") is False,
        "metadata_db_writes": metadata_details.get("db_writes") is False,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise PhaseC1Error("blocked_unsafe_local_details_flags", ",".join(failed))


def _rows_by_media_id(rows: Iterable[Mapping[str, Any]]) -> dict[int, Mapping[str, Any]]:
    result: dict[int, Mapping[str, Any]] = {}
    for row in rows:
        if "media_id" in row:
            result[int(row["media_id"])] = row
    return result


def _provider_index_label(index_name: str | None) -> str | None:
    if not index_name:
        return None
    if "Danbooru" in index_name:
        return "Danbooru"
    return index_name.split(" - ", 1)[0].strip()


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_url_host_path(value: Any) -> tuple[str | None, str | None]:
    url = _as_text(value)
    if not url:
        return None, None
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return None, None
    return parsed.netloc.lower(), parsed.path or None


def _post_url_path_for_source(source_host: str | None, result_id: str | None) -> str | None:
    if source_host == "danbooru.donmai.us" and result_id:
        return f"/posts/{result_id}"
    return None


def _first_source_host(hosts: Any) -> str | None:
    if isinstance(hosts, list):
        if "danbooru.donmai.us" in hosts:
            return "danbooru.donmai.us"
        for host in hosts:
            if host:
                return str(host)
    if hosts:
        return str(hosts)
    return None


def _top_result_from_live_item(live_item: Mapping[str, Any]) -> Mapping[str, Any]:
    provider_result = live_item.get("provider_result")
    provider_result = provider_result if isinstance(provider_result, Mapping) else {}
    normalized_payload = provider_result.get("normalized_payload")
    normalized_payload = normalized_payload if isinstance(normalized_payload, Mapping) else {}
    top_result = normalized_payload.get("top_result") or live_item.get("top_result")
    return top_result if isinstance(top_result, Mapping) else {}


def _identity_from_live_item(live_item: Mapping[str, Any]) -> dict[str, str | None]:
    top_result = _top_result_from_live_item(live_item)
    request_shape = live_item.get("request_shape_redacted")
    request_shape = request_shape if isinstance(request_shape, Mapping) else {}
    source_url = _as_text(top_result.get("source_url") or live_item.get("source_url"))
    post_url = _as_text(top_result.get("post_url") or live_item.get("post_url"))
    source_url_host, source_url_path = _safe_url_host_path(source_url)
    post_url_host, post_url_path = _safe_url_host_path(post_url)
    result_id = _as_text(
        top_result.get("result_id")
        or top_result.get("post_id")
        or top_result.get("provider_external_id")
        or live_item.get("result_id")
        or live_item.get("post_id")
        or live_item.get("provider_external_id")
    )
    source_host = _as_text(top_result.get("source_url_host") or live_item.get("source_url_host")) or source_url_host or post_url_host
    return {
        "provider_key": _as_text(request_shape.get("provider_key") or live_item.get("provider_key")),
        "result_id": result_id,
        "source_host": source_host,
        "provider_index_label": _provider_index_label(_as_text(top_result.get("index_name") or live_item.get("index_name"))),
        "source_url_host": source_url_host,
        "source_url_path": source_url_path,
        "post_url_host": post_url_host,
        "post_url_path": post_url_path or _post_url_path_for_source(source_host, result_id),
    }


def _identity_from_metadata_row(metadata_row: Mapping[str, Any]) -> dict[str, str | None]:
    top_result = metadata_row.get("top_result")
    top_result = top_result if isinstance(top_result, Mapping) else {}
    source_url = _as_text(top_result.get("source_url") or metadata_row.get("source_url"))
    post_url = _as_text(top_result.get("post_url") or metadata_row.get("post_url"))
    source_url_host, source_url_path = _safe_url_host_path(source_url)
    post_url_host, post_url_path = _safe_url_host_path(post_url)
    result_id = _as_text(
        top_result.get("result_id")
        or top_result.get("post_id")
        or top_result.get("provider_external_id")
        or metadata_row.get("result_id")
        or metadata_row.get("post_id")
        or metadata_row.get("provider_external_id")
    )
    source_host = _first_source_host(top_result.get("source_url_hosts"))
    source_host = _as_text(source_host or top_result.get("source_url_host") or metadata_row.get("source_url_host"))
    source_host = source_host or source_url_host or post_url_host
    return {
        "provider_key": _as_text(top_result.get("provider_key") or metadata_row.get("provider_key") or PROVIDER),
        "result_id": result_id,
        "source_host": source_host,
        "provider_index_label": _provider_index_label(_as_text(top_result.get("index_name") or metadata_row.get("index_name"))),
        "source_url_host": source_url_host,
        "source_url_path": source_url_path,
        "post_url_host": post_url_host,
        "post_url_path": post_url_path or _post_url_path_for_source(source_host, result_id),
    }


def _require_unique_requested_media_ids(requested: tuple[int, ...]) -> None:
    duplicates = sorted({media_id for media_id in requested if requested.count(media_id) > 1})
    if duplicates:
        raise PhaseC1Error("duplicate_media_id", json.dumps(duplicates))


def _identity_value_mismatch(left: str | None, right: str | None) -> bool:
    return bool(left and right and left != right)


def _require_live_metadata_identity_match(
    *,
    media_id: int,
    live_identity: Mapping[str, str | None],
    metadata_identity: Mapping[str, str | None],
) -> None:
    if not live_identity.get("result_id") or not live_identity.get("source_host"):
        raise PhaseC1Error("live_identity_missing", f"media_id={media_id}")
    if not metadata_identity.get("result_id") or not metadata_identity.get("source_host"):
        raise PhaseC1Error("metadata_identity_missing", f"media_id={media_id}")
    for key in ("provider_key", "result_id", "source_host", "provider_index_label"):
        if _identity_value_mismatch(live_identity.get(key), metadata_identity.get(key)):
            raise PhaseC1Error("live_metadata_identity_mismatch", f"media_id={media_id}:{key}")
    for host_key, path_key in (("source_url_host", "source_url_path"), ("post_url_host", "post_url_path")):
        if _identity_value_mismatch(live_identity.get(host_key), metadata_identity.get(host_key)):
            raise PhaseC1Error("live_metadata_identity_mismatch", f"media_id={media_id}:{host_key}")
        if _identity_value_mismatch(live_identity.get(path_key), metadata_identity.get(path_key)):
            raise PhaseC1Error("live_metadata_identity_mismatch", f"media_id={media_id}:{path_key}")


def _require_approved_result_identity(
    *,
    media_id: int,
    live_identity: Mapping[str, str | None],
    metadata_identity: Mapping[str, str | None],
) -> None:
    expected = APPROVED_RESULT_IDENTITIES.get(media_id)
    if not expected:
        raise PhaseC1Error("blocked_unapproved_media_ids", str(media_id))
    if not live_identity.get("result_id") and not metadata_identity.get("result_id"):
        raise PhaseC1Error("approved_result_id_missing", f"media_id={media_id}")
    for identity in (live_identity, metadata_identity):
        if identity.get("result_id") and identity.get("result_id") != expected["result_id"]:
            raise PhaseC1Error("approval_result_identity_mismatch", f"media_id={media_id}:result_id")
        if identity.get("provider_key") and identity.get("provider_key") != expected["provider_key"]:
            raise PhaseC1Error("source_identity_mismatch", f"media_id={media_id}:provider_key")
        if identity.get("source_host") and identity.get("source_host") != expected["source_host"]:
            raise PhaseC1Error("source_identity_mismatch", f"media_id={media_id}:source_host")
        if identity.get("provider_index_label") and identity.get("provider_index_label") != expected["provider_index_label"]:
            raise PhaseC1Error("source_identity_mismatch", f"media_id={media_id}:provider_index_label")
        for path_key in ("source_url_path", "post_url_path"):
            path = identity.get(path_key)
            if path and path != expected["post_url_path"]:
                raise PhaseC1Error("source_identity_mismatch", f"media_id={media_id}:{path_key}")


def _manual_item(
    media_id: int,
    *,
    live_identity: Mapping[str, str | None],
    metadata_identity: Mapping[str, str | None],
) -> dict[str, Any]:
    if media_id in APPROVED_MEDIA_IDS:
        _require_approved_result_identity(
            media_id=media_id,
            live_identity=live_identity,
            metadata_identity=metadata_identity,
        )
        return {
            "media_id": media_id,
            "judgment": "correct",
            "metadata_useful": True,
            "recommended_action": "keep_as_strong_evidence",
        }
    return {
        "media_id": media_id,
        "judgment": "wrong_unrelated",
        "metadata_useful": False,
        "recommended_action": "discard",
    }


def _require_c1_write_promotion_ready(plan: EvidencePersistencePlan) -> None:
    expected = APPROVED_RESULT_IDENTITIES.get(plan.media_id)
    if not expected:
        raise PhaseC1Error("blocked_unapproved_media_ids", str(plan.media_id))
    if plan.media_id in LOW_CONFIDENCE_EXCLUDED_IDS:
        raise PhaseC1Error("blocked_low_confidence_positive_write", f"media_id={plan.media_id}")

    query = plan.provider_query
    source = plan.source_match
    metadata = plan.extracted_metadata
    mismatches = []
    if query.media_id != plan.media_id:
        mismatches.append("provider_query.media_id")
    if source.media_id != plan.media_id:
        mismatches.append("source_match.media_id")
    if query.provider_key != source.provider_key or query.provider_key != expected["provider_key"]:
        mismatches.append("provider_key")
    if mismatches:
        raise PhaseC1Error("nested_plan_identity_mismatch", f"media_id={plan.media_id}:{','.join(mismatches)}")

    if source.provider_result_id != expected["result_id"]:
        raise PhaseC1Error("approval_result_identity_mismatch", f"media_id={plan.media_id}:result_id")
    if source.source_host != expected["source_host"]:
        raise PhaseC1Error("source_identity_mismatch", f"media_id={plan.media_id}:source_host")
    if source.provider_index != expected["provider_index_label"]:
        raise PhaseC1Error("source_identity_mismatch", f"media_id={plan.media_id}:provider_index")
    for url_name, url_value in (("source_url", source.source_url), ("post_url", source.post_url)):
        host, path = _safe_url_host_path(url_value)
        if host and host != expected["source_host"]:
            raise PhaseC1Error("source_identity_mismatch", f"media_id={plan.media_id}:{url_name}_host")
        if path and path != expected["post_url_path"]:
            raise PhaseC1Error("source_identity_mismatch", f"media_id={plan.media_id}:{url_name}_path")

    if query.query_hash_status != "present_valid" or not query.query_hash:
        raise PhaseC1Error("missing_or_invalid_query_hash", f"media_id={plan.media_id}")
    if query.request_shape_status != "present" or not query.request_shape_redacted:
        raise PhaseC1Error("missing_or_invalid_request_shape", f"media_id={plan.media_id}")
    if plan.provider_provenance_status != "ready" or not plan.provider_cache_persistence_allowed:
        raise PhaseC1Error("provider_provenance_not_ready", f"media_id={plan.media_id}")
    if source.source_identifier_status != "present":
        raise PhaseC1Error("missing_source_identifier", f"media_id={plan.media_id}")
    if source.match_class != SourceMatchClass.exact_or_near_exact:
        raise PhaseC1Error("unsupported_match_class", f"media_id={plan.media_id}")
    if source.evidence_strength != EvidenceStrength.strong:
        raise PhaseC1Error("unsupported_evidence_strength", f"media_id={plan.media_id}")
    if source.manual_validation_status != ManualValidationStatus.validated_correct:
        raise PhaseC1Error("manual_validation_not_validated_correct", f"media_id={plan.media_id}")
    if metadata.localization_status != LocalizationStatus.pending or not plan.localization_pending:
        raise PhaseC1Error("localization_not_pending", f"media_id={plan.media_id}")
    if plan.confirmed_assignment_allowed:
        raise PhaseC1Error("confirmed_assignment_allowed", f"media_id={plan.media_id}")
    if plan.entity_auto_create_allowed:
        raise PhaseC1Error("entity_auto_create_allowed", f"media_id={plan.media_id}")
    if not plan.provider_cache_planned or not plan.entity_evidence_planned:
        raise PhaseC1Error("positive_persistence_not_planned", f"media_id={plan.media_id}")

    assert_public_payload_safe(query.request_shape_redacted)
    assert_public_payload_safe(source.to_public_dict())
    assert_public_payload_safe(metadata.to_public_dict())


def promote_c1_plan_for_db_write(plan: EvidencePersistencePlan) -> EvidencePersistencePlan:
    # C0 mapper output is non-mutating by default; C1 promotes only approved
    # manually validated plans after every phase gate has passed.
    _require_c1_write_promotion_ready(plan)
    return replace(
        plan,
        db_write_allowed=True,
        notes=tuple(plan.notes)
        + ("C1 approved validated evidence persistence; DB write allowed after phase gates.",),
    )


def build_phase44c1_plans(
    *,
    live_details: Mapping[str, Any],
    metadata_details: Mapping[str, Any],
    media_ids: Iterable[int],
) -> list[Any]:
    requested = tuple(int(media_id) for media_id in media_ids)
    _require_unique_requested_media_ids(requested)
    if set(requested) != set(APPROVED_MEDIA_IDS):
        raise PhaseC1Error("blocked_unapproved_media_ids", json.dumps(sorted(requested)))

    live_by_id = _rows_by_media_id(live_details.get("provider_results") or [])
    metadata_by_id = _rows_by_media_id(metadata_details.get("provider_results") or [])
    plans = []
    for media_id in requested:
        live_item = live_by_id.get(media_id)
        metadata_row = metadata_by_id.get(media_id)
        if not live_item or not metadata_row:
            raise PhaseC1Error("blocked_missing_local_details", f"media_id={media_id}")
        live_identity = _identity_from_live_item(live_item)
        metadata_identity = _identity_from_metadata_row(metadata_row)
        if not live_identity.get("result_id") and not metadata_identity.get("result_id"):
            raise PhaseC1Error("approved_result_id_missing", f"media_id={media_id}")
        _require_live_metadata_identity_match(
            media_id=media_id,
            live_identity=live_identity,
            metadata_identity=metadata_identity,
        )
        top_result = metadata_row.get("top_result") or {}
        source_host = _first_source_host(top_result.get("source_url_hosts"))
        metadata_item = {
            "media_id": media_id,
            "provider_index_label": _provider_index_label(top_result.get("index_name")),
            "source_url_host": source_host,
            "result_id": top_result.get("result_id"),
            "artist": top_result.get("creator"),
            "work_or_copyright": top_result.get("material") or [],
            "characters": top_result.get("characters") or [],
            "general_tags": top_result.get("general_tags") or [],
            "metadata_extraction_status": "requery_performed",
        }
        assert_public_payload_safe(metadata_item)
        plan = map_saucenao_result_to_plan(
            live_item=deepcopy(live_item),
            manual_item=_manual_item(
                media_id,
                live_identity=live_identity,
                metadata_identity=metadata_identity,
            ),
            metadata_item=metadata_item,
        )
        plans.append(promote_c1_plan_for_db_write(plan))
    return plans


def load_settings_and_engine():
    explicit_env = os.environ.get("VIOLET_ENV")
    if explicit_env != "development":
        raise PhaseC1Error("identity_blocked", f"VIOLET_ENV must be explicitly development, got {explicit_env!r}")
    if os.environ.get("TEST_DATABASE_URL"):
        raise PhaseC1Error("identity_blocked", "TEST_DATABASE_URL must be unset")

    from app.config import settings  # noqa: WPS433

    if settings.VIOLET_ENV != "development" or settings.IS_TEST_ENV or settings.IS_PRODUCTION_ENV:
        raise PhaseC1Error("identity_blocked", f"settings.VIOLET_ENV={settings.VIOLET_ENV!r}")
    if settings.DB_NAME != "blombooru":
        raise PhaseC1Error("identity_blocked", f"configured DB_NAME={settings.DB_NAME!r}")
    if os.environ.get("POSTGRES_DB", "").strip().lower() == "blombooru_test":
        raise PhaseC1Error("identity_blocked", "POSTGRES_DB points to blombooru_test")
    test_storage = getattr(settings, "VIOLET_TEST_STORAGE_ROOT", None)
    if test_storage and settings.STORAGE_ROOT.resolve() == test_storage.resolve():
        raise PhaseC1Error("identity_blocked", "storage root equals VIOLET_TEST_STORAGE_ROOT")
    if settings.STORAGE_ROOT.name.lower() == "test":
        raise PhaseC1Error("identity_blocked", "storage root name looks like test storage")

    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT current_database(), current_user, inet_server_addr()::text, inet_server_port()")
        ).one()
    current_db = row[0]
    if current_db != "blombooru":
        engine.dispose()
        raise PhaseC1Error("identity_blocked", f"current_database={current_db!r}")
    identity = {
        "violet_env": settings.VIOLET_ENV,
        "configured_db": settings.DB_NAME,
        "current_database": current_db,
        "db_host": settings.DB_HOST,
        "db_port": settings.DB_PORT,
        "db_user": settings.DB_USER,
        "db_auth_hidden": True,
        "server_addr": row[2],
        "server_port": row[3],
        "storage_root_basename": settings.STORAGE_ROOT.name,
        "storage_root_is_test_storage": False,
        "test_database_url_set": False,
    }
    assert_public_payload_safe(identity)
    return settings, engine, identity


def find_pg_tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    candidate = Path("C:/Program Files/PostgreSQL/17/bin") / f"{name}.exe"
    if candidate.exists():
        return str(candidate)
    return None


def redact_process_output(text_value: str, password: str | None) -> str:
    result = text_value
    if password:
        result = result.replace(password, "<redacted>")
    return result[:1000]


def create_pg_dump_backup(settings: Any, backup_path: Path) -> dict[str, Any]:
    pg_dump = find_pg_tool("pg_dump")
    if not pg_dump:
        raise PhaseC1Error("backup_failed", "pg_dump_not_found")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    if settings.DB_PASSWORD:
        env["PGPASSWORD"] = settings.DB_PASSWORD
    cmd = [
        pg_dump,
        "-h",
        settings.DB_HOST,
        "-p",
        str(settings.DB_PORT),
        "-U",
        settings.DB_USER,
        "-Fc",
        "-f",
        str(backup_path),
        settings.DB_NAME,
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=180)
    if completed.returncode != 0:
        raise PhaseC1Error("backup_failed", redact_process_output(completed.stderr or completed.stdout, settings.DB_PASSWORD))
    size = backup_path.stat().st_size if backup_path.exists() else 0
    if size <= 0:
        raise PhaseC1Error("backup_failed", "pg_dump_empty")

    pg_restore = find_pg_tool("pg_restore")
    toc_verified = False
    if pg_restore:
        verify = subprocess.run([pg_restore, "-l", str(backup_path)], capture_output=True, text=True, timeout=60)
        if verify.returncode != 0:
            raise PhaseC1Error("backup_failed", redact_process_output(verify.stderr or verify.stdout, settings.DB_PASSWORD))
        toc_verified = True

    return {
        "basename": backup_path.name,
        "bytes": size,
        "format": "pg_dump -Fc",
        "toc_verified": toc_verified,
        "path_redacted": True,
        "local_path": str(backup_path),
    }


def _plan_query_hashes(plans: Iterable[Any]) -> list[str]:
    return [plan.provider_query.query_hash for plan in plans if plan.provider_query.query_hash]


def _plan_payload_refs(plans: Iterable[Any]) -> list[str]:
    return [provider_cache_payload_ref(plan) for plan in plans]


def _provider_cache_is_c1(row: ProviderCache, payload_refs: set[str]) -> bool:
    payload = row.response_json_redacted if isinstance(row.response_json_redacted, Mapping) else {}
    return payload.get("phase") == PHASE and payload.get("provider_cache_payload_ref") in payload_refs


def _evidence_is_c1(row: EntityEvidence, payload_refs: set[str]) -> bool:
    return (
        row.payload_ref in payload_refs
        and bool(row.summary)
        and str(row.summary).startswith(f"Phase {PHASE} validated provider evidence")
    )


def collect_db_state(db: Session, *, plans: list[Any], low_confidence_query_hashes: list[str]) -> dict[str, Any]:
    approved_ids = [plan.media_id for plan in plans]
    qhashes = _plan_query_hashes(plans)
    payload_refs = set(_plan_payload_refs(plans))
    provider_cache_rows = (
        db.query(ProviderCache)
        .filter(
            ProviderCache.provider == PROVIDER,
            ProviderCache.query_type == QUERY_TYPE,
            ProviderCache.query_hash.in_(qhashes),
        )
        .all()
        if qhashes
        else []
    )
    c1_provider_cache_rows = [row for row in provider_cache_rows if _provider_cache_is_c1(row, payload_refs)]
    evidence_rows = (
        db.query(EntityEvidence)
        .filter(
            EntityEvidence.provider == PROVIDER,
            EntityEvidence.source_type == "external",
            EntityEvidence.evidence_type == "reverse_search",
            EntityEvidence.query_hash.in_(qhashes),
        )
        .all()
        if qhashes
        else []
    )
    c1_evidence_rows = [row for row in evidence_rows if _evidence_is_c1(row, payload_refs)]
    evidence_ids = [row.id for row in c1_evidence_rows]
    c1_candidate_count = (
        db.query(MediaEntityCandidate).filter(MediaEntityCandidate.evidence_id.in_(evidence_ids)).count()
        if evidence_ids
        else 0
    )
    approved_candidate_total = db.query(MediaEntityCandidate).filter(MediaEntityCandidate.media_id.in_(approved_ids)).count()
    return {
        "approved_media_present": db.query(Media).filter(Media.id.in_(approved_ids)).count(),
        "provider_cache_approved": len(c1_provider_cache_rows),
        "provider_cache_unrelated_existing_ignored": len(provider_cache_rows) - len(c1_provider_cache_rows),
        "entity_evidence_approved": len(c1_evidence_rows),
        "entity_evidence_unrelated_existing_ignored": len(evidence_rows) - len(c1_evidence_rows),
        "media_entity_candidates_c1": c1_candidate_count,
        "media_entity_candidates_unrelated_existing_ignored": max(0, approved_candidate_total - c1_candidate_count),
        "media_entity_assignments_for_approved": db.query(MediaEntityAssignment)
        .filter(MediaEntityAssignment.media_id.in_(approved_ids))
        .count(),
        "entity_count": db.query(Entity).count(),
        "tag_translation_count": db.query(TagTranslation).count(),
        "media_tags_for_approved": db.execute(
            select(func.count()).select_from(blombooru_media_tags).where(blombooru_media_tags.c.media_id.in_(approved_ids))
        ).scalar_one(),
        "low_confidence_provider_cache": db.query(ProviderCache)
        .filter(
            ProviderCache.provider == PROVIDER,
            ProviderCache.query_type == QUERY_TYPE,
            ProviderCache.query_hash.in_(low_confidence_query_hashes),
        )
        .count()
        if low_confidence_query_hashes
        else 0,
        "low_confidence_positive_evidence": db.query(EntityEvidence)
        .filter(
            EntityEvidence.provider == PROVIDER,
            EntityEvidence.evidence_type == "reverse_search",
            EntityEvidence.media_id.in_(LOW_CONFIDENCE_EXCLUDED_IDS),
        )
        .count(),
        "low_confidence_candidates": db.query(MediaEntityCandidate)
        .filter(MediaEntityCandidate.media_id.in_(LOW_CONFIDENCE_EXCLUDED_IDS))
        .count(),
    }


def low_confidence_query_hashes(live_details: Mapping[str, Any]) -> list[str]:
    live_by_id = _rows_by_media_id(live_details.get("provider_results") or [])
    return [live_by_id[media_id].get("query_hash") for media_id in LOW_CONFIDENCE_EXCLUDED_IDS if live_by_id.get(media_id)]


def ensure_media_rows_present(state: Mapping[str, Any], expected_count: int) -> None:
    if state["approved_media_present"] != expected_count:
        raise PhaseC1Error(
            "identity_blocked",
            f"approved media row count {state['approved_media_present']} != {expected_count}",
        )


def build_rollback_sql(query_hashes: list[str]) -> str:
    quoted = ", ".join(f"'{query_hash}'" for query_hash in query_hashes)
    return "\n".join(
        [
            "BEGIN;",
            "DELETE FROM blombooru_media_entity_candidates",
            "WHERE evidence_id IN (",
            "  SELECT id FROM blombooru_entity_evidence",
            f"  WHERE provider = '{PROVIDER}' AND query_hash IN ({quoted})",
            f"    AND payload_ref LIKE 'provider_cache:{PROVIDER}:{QUERY_TYPE}:%'",
            "    AND summary LIKE 'Phase 4.4-C1 validated provider evidence%'",
            ");",
            "DELETE FROM blombooru_entity_evidence",
            f"WHERE provider = '{PROVIDER}' AND query_hash IN ({quoted})",
            f"  AND payload_ref LIKE 'provider_cache:{PROVIDER}:{QUERY_TYPE}:%'",
            "  AND summary LIKE 'Phase 4.4-C1 validated provider evidence%';",
            "DELETE FROM blombooru_provider_cache",
            f"WHERE provider = '{PROVIDER}' AND query_type = '{QUERY_TYPE}'",
            f"  AND query_hash IN ({quoted})",
            f"  AND response_json_redacted->>'phase' = '{PHASE}';",
            "COMMIT;",
        ]
    )


def expected_persistence_counts(plans: Iterable[Any]) -> dict[str, int]:
    plan_list = list(plans)
    return {
        "ProviderCache": len(plan_list),
        "EntityEvidence": len(plan_list),
        "MediaEntityCandidate": sum(len(plan.planned_entity_candidates) for plan in plan_list),
    }


def build_post_write_verification(
    db_before: Mapping[str, Any],
    db_after: Mapping[str, Any],
    plans: Iterable[Any],
) -> dict[str, Any]:
    expected = expected_persistence_counts(plans)
    low_confidence_positive_evidence_inserted = (
        db_after["low_confidence_positive_evidence"] - db_before["low_confidence_positive_evidence"]
    )
    low_confidence_candidates_inserted = db_after["low_confidence_candidates"] - db_before["low_confidence_candidates"]
    checks = {
        "provider_cache_count_matches_expected": db_after["provider_cache_approved"] == expected["ProviderCache"],
        "entity_evidence_count_matches_expected": db_after["entity_evidence_approved"] == expected["EntityEvidence"],
        "media_entity_candidate_count_matches_expected": db_after["media_entity_candidates_c1"]
        == expected["MediaEntityCandidate"],
        "confirmed_assignment_count_is_zero": db_after["media_entity_assignments_for_approved"] == 0,
        "entity_count_unchanged": db_before["entity_count"] == db_after["entity_count"],
        "tag_translation_count_unchanged": db_before["tag_translation_count"] == db_after["tag_translation_count"],
        "media_tags_for_approved_unchanged": db_before["media_tags_for_approved"] == db_after["media_tags_for_approved"],
        "low_confidence_positive_evidence_inserted_by_c1_zero": low_confidence_positive_evidence_inserted == 0,
        "low_confidence_candidates_inserted_by_c1_zero": low_confidence_candidates_inserted == 0,
    }
    failure_codes = [name for name, passed in checks.items() if not passed]
    return {
        "success": not failure_codes,
        "failure_codes": failure_codes,
        "expected_counts": expected,
        "provider_cache_approved_count": db_after["provider_cache_approved"],
        "entity_evidence_approved_count": db_after["entity_evidence_approved"],
        "media_entity_candidate_c1_count": db_after["media_entity_candidates_c1"],
        "provider_cache_unrelated_existing_ignored": db_after.get("provider_cache_unrelated_existing_ignored", 0),
        "entity_evidence_unrelated_existing_ignored": db_after.get("entity_evidence_unrelated_existing_ignored", 0),
        "media_entity_candidates_unrelated_existing_ignored": db_after.get(
            "media_entity_candidates_unrelated_existing_ignored", 0
        ),
        "confirmed_assignment_count_for_approved": db_after["media_entity_assignments_for_approved"],
        "low_confidence_positive_evidence_total_before": db_before["low_confidence_positive_evidence"],
        "low_confidence_positive_evidence_total_after": db_after["low_confidence_positive_evidence"],
        "low_confidence_positive_evidence_inserted_by_c1": low_confidence_positive_evidence_inserted,
        "low_confidence_candidates_total_before": db_before["low_confidence_candidates"],
        "low_confidence_candidates_total_after": db_after["low_confidence_candidates"],
        "low_confidence_candidates_inserted_by_c1": low_confidence_candidates_inserted,
        **checks,
    }


def build_idempotency_verification(
    idempotency_summary: Mapping[str, Any] | None,
    plans: Iterable[Any],
) -> dict[str, Any]:
    expected = expected_persistence_counts(plans)
    if idempotency_summary is None:
        return {
            "status": "not_run",
            "idempotency_check_ran": False,
            "idempotency_success": False,
            "success": False,
            "failure_codes": ["idempotency_verification_not_run"],
            "expected_counts": expected,
            "would_insert_provider_cache": None,
            "would_insert_entity_evidence": None,
            "would_insert_media_entity_candidate": None,
        }

    counts = idempotency_summary.get("counts", {})
    failure_codes = []
    if idempotency_summary.get("success") is not True:
        failure_codes.append("idempotency_dry_run_failed")
    for table, expected_count in expected.items():
        table_counts = counts.get(table, {})
        if table_counts.get("inserted", 0) != 0:
            failure_codes.append(f"{table}_would_insert")
        if table_counts.get("existing", 0) != expected_count:
            failure_codes.append(f"{table}_existing_count_mismatch")
        if table_counts.get("planned", 0) != expected_count:
            failure_codes.append(f"{table}_planned_count_mismatch")
    return {
        "status": "dry_run",
        "idempotency_check_ran": True,
        "idempotency_success": not failure_codes,
        "success": not failure_codes,
        "failure_codes": failure_codes,
        "expected_counts": expected,
        "counts": counts,
        "would_insert_provider_cache": counts.get("ProviderCache", {}).get("inserted", 0),
        "would_insert_entity_evidence": counts.get("EntityEvidence", {}).get("inserted", 0),
        "would_insert_media_entity_candidate": counts.get("MediaEntityCandidate", {}).get("inserted", 0),
        "existing_provider_cache": counts.get("ProviderCache", {}).get("existing", 0),
        "existing_entity_evidence": counts.get("EntityEvidence", {}).get("existing", 0),
        "existing_media_entity_candidate": counts.get("MediaEntityCandidate", {}).get("existing", 0),
    }


def _blocked_items_by_media(persistence: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    blocked: dict[int, Mapping[str, Any]] = {}
    for item in persistence.get("items", []):
        if item.get("status") == "blocked" and item.get("media_id") is not None:
            blocked[int(item["media_id"])] = item
    return blocked


def apply_plans_with_pre_commit_gates(
    db: Session,
    *,
    plans: list[Any],
    db_before: Mapping[str, Any],
    low_confidence_query_hashes: list[str],
) -> dict[str, Any]:
    """Apply C1 writes and run blocking gates before the caller commits."""
    persistence = persist_provider_evidence_plans(
        db,
        plans,
        apply=True,
        options=EvidencePersistenceOptions(write_candidates=True, strict=True),
    )
    db_after = collect_db_state(db, plans=plans, low_confidence_query_hashes=low_confidence_query_hashes)
    post_write_verification = build_post_write_verification(db_before, db_after, plans)
    if post_write_verification["success"] is not True:
        raise PhaseC1Error(
            "post_write_verification_failed",
            json.dumps(post_write_verification["failure_codes"], sort_keys=True),
        )
    idempotency_summary = persist_provider_evidence_plans(
        db,
        plans,
        apply=False,
        options=EvidencePersistenceOptions(write_candidates=True, strict=True),
    )
    idempotency_verification = build_idempotency_verification(idempotency_summary, plans)
    if idempotency_verification["success"] is not True:
        raise PhaseC1Error(
            "idempotency_verification_failed",
            json.dumps(idempotency_verification["failure_codes"], sort_keys=True),
        )
    return {
        "persistence": persistence,
        "db_after": db_after,
        "post_write_verification": post_write_verification,
        "idempotency_summary": idempotency_summary,
    }


def build_public_summary(
    *,
    mode: str,
    identity: Mapping[str, Any],
    plans: list[Any],
    persistence: Mapping[str, Any],
    db_before: Mapping[str, Any],
    db_after: Mapping[str, Any],
    backup: Mapping[str, Any] | None,
    idempotency_summary: Mapping[str, Any] | None = None,
    post_write_verification: Mapping[str, Any] | None = None,
    blocked_status: str | None = None,
) -> dict[str, Any]:
    query_hashes = _plan_query_hashes(plans)
    per_media = {}
    blocked_items = _blocked_items_by_media(persistence)
    for plan in plans:
        blocked_item = blocked_items.get(plan.media_id)
        if blocked_item:
            per_media[str(plan.media_id)] = {
                "provider": plan.provider_query.provider_key,
                "query_hash_present": bool(plan.provider_query.query_hash),
                "status": "blocked",
                "blocked_reason": blocked_item.get("blocked_reason"),
            }
        else:
            per_media[str(plan.media_id)] = {
                "provider": plan.provider_query.provider_key,
                "query_hash_present": bool(plan.provider_query.query_hash),
                "source_host": plan.source_match.source_host,
                "post_url": plan.source_match.post_url,
                "result_id": plan.source_match.provider_result_id,
                "score": plan.source_match.score_value,
                "minimum_similarity": plan.source_match.provider_minimum_similarity,
                "manual_validation_status": plan.source_match.manual_validation_status.value,
                "evidence_strength": plan.source_match.evidence_strength.value,
                "localization_status": plan.extracted_metadata.localization_status.value,
                "artist": list(plan.extracted_metadata.artist_raw),
                "work": list(plan.extracted_metadata.work_raw),
                "character": list(plan.extracted_metadata.character_raw),
            }
    post_write_verification = post_write_verification or build_post_write_verification(db_before, db_after, plans)
    idempotency_verification = build_idempotency_verification(idempotency_summary, plans) if mode == "apply" else None
    final_success = (
        persistence.get("success") is True
        and post_write_verification["success"] is True
        and (mode != "apply" or idempotency_verification["success"] is True)
    )
    blocked_reasons = []
    if persistence.get("success") is not True:
        blocked_reasons.append("persistence_plan_or_write_failed")
    blocked_reasons.extend(post_write_verification["failure_codes"])
    if idempotency_verification:
        blocked_reasons.extend(idempotency_verification["failure_codes"])
    status = blocked_status or ("applied" if mode == "apply" else "dry_run")
    if not final_success:
        status = blocked_status or "blocked"
    summary = {
        "phase": PHASE,
        "status": status,
        "success": final_success,
        "blocked_reasons": blocked_reasons,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "approved_media_ids": list(APPROVED_MEDIA_IDS),
        "approved_result_identities": {
            str(media_id): dict(identity) for media_id, identity in APPROVED_RESULT_IDENTITIES.items()
        },
        "low_confidence_excluded_media_ids": list(LOW_CONFIDENCE_EXCLUDED_IDS),
        "source_of_truth": "local_ignored_B1_B1V_details_artifacts_plus_C1_approved_manual_validation_scope",
        "artifact_lifecycle": {
            "persistence_service_code": "durable_provider_evidence_infrastructure",
            "c1_runner": "phase_scoped_operational_runner",
            "reports": "public_report_handoff_roadmap_update",
            "backup_and_local_details": "one_off_local_ignored_artifact",
        },
        "db_identity": dict(identity),
        "backup": None
        if backup is None
        else {
            "basename": backup["basename"],
            "bytes": backup["bytes"],
            "format": backup["format"],
            "toc_verified": backup["toc_verified"],
            "path_redacted": True,
        },
        "provider_cache_written": persistence["counts"]["ProviderCache"]["inserted"] > 0,
        "entity_evidence_written": persistence["counts"]["EntityEvidence"]["inserted"] > 0,
        "media_entity_candidate_written": persistence["counts"]["MediaEntityCandidate"]["inserted"] > 0,
        "media_entity_candidate_deferred": persistence.get("candidate_deferred_schema_constraint") is True,
        "counts": persistence["counts"],
        "per_media": per_media,
        "db_state_before": dict(db_before),
        "db_state_after": dict(db_after),
        "post_write_verification": post_write_verification,
        "idempotency_verification": idempotency_verification,
        "closeout": {
            "manual_approval_bound_to_provider_result_ids": {
                str(media_id): identity["result_id"] for media_id, identity in APPROVED_RESULT_IDENTITIES.items()
            },
            "db_write_allowed_enforced_by_persistence_service": True,
            "approved_c1_plans_promoted_to_db_write_allowed": all(plan.db_write_allowed is True for plan in plans),
            "live_metadata_identity_match_verified_before_plan": True,
            "nested_plan_identity_validation_before_write": True,
            "duplicate_media_ids_rejected_before_plan_backup_apply": True,
            "apply_mode_strict_regardless_of_strict_flag": True,
            "abort_before_apply_if_dry_run_summary_unsuccessful": True,
            "post_write_gates_run_before_commit": mode == "apply",
            "commit_only_after_post_write_and_idempotency_success": mode == "apply",
            "audit_artifacts_written_before_commit": mode == "apply"
            and persistence.get("success") is True
            and blocked_status is None,
            "low_confidence_check_scope": "before_after_delta_for_excluded_media_ids",
            "approved_verification_scope": "exact_c1_payload_ref_and_phase_summary",
            "final_success_depends_on_post_write_verification": True,
            "final_success_depends_on_idempotency_verification": mode == "apply",
            "runner_performs_post_apply_idempotency_check": mode == "apply" and idempotency_summary is not None,
            "idempotency_check_phase": "pre_commit_after_apply_transactional_dry_run" if mode == "apply" else "not_applicable",
            "new_db_writes_during_closeout": any(
                persistence["counts"][table]["inserted"] > 0
                for table in ("ProviderCache", "EntityEvidence", "MediaEntityCandidate")
            ),
            "current_db_state_after_fix": dict(db_after),
            "deferred_hardening_items": [
                "pre_existing_candidate_conflict_dry_run_detection",
                "non_suggested_candidate_decision_preservation_on_rerun",
                "dry_run_post_write_count_semantics",
                "provider_cache_query_scoped_payload_redesign_for_duplicate_images",
                "broader_candidate_lifecycle_hardening",
                "caller_owned_transaction_rollback_policy_for_future_service_callers",
            ],
        },
        "rollback": {
            "backup_restore_note": "Use the local ignored pg_dump custom archive basename listed here; full path is kept only in local details.",
            "delete_sql_for_c1_rows": build_rollback_sql(query_hashes),
        },
        "safety_confirmation": {
            "provider_call": False,
            "upload": False,
            "db_migration": False,
            "source_icloud_mutation": False,
            "app_managed_storage_mutation": False,
            "localization_execution": False,
            "entity_resolver": False,
            "similarity_clustering": False,
            "confirmed_assignment_created": not post_write_verification["confirmed_assignment_count_is_zero"],
            "automatic_entity_created": not post_write_verification["entity_count_unchanged"],
            "media_tags_mutated": not post_write_verification["media_tags_for_approved_unchanged"],
            "tag_translation_mutated": not post_write_verification["tag_translation_count_unchanged"],
            "low_confidence_positive_writes": not post_write_verification[
                "low_confidence_positive_evidence_inserted_by_c1_zero"
            ],
        },
    }
    assert_public_payload_safe(summary)
    return summary


def render_markdown(summary: Mapping[str, Any]) -> str:
    counts = summary["counts"]
    backup = summary.get("backup")
    lines = [
        "# Phase 4.4-C1 - Validated Evidence Persistence",
        "",
        f"Date: {summary['generated_at']}",
        "",
        "## Summary",
        "",
        "Phase 4.4-C1 persisted only the two manually validated high-confidence SauceNAO source matches through the provider-neutral evidence contract.",
        "",
        f"- Approved media IDs: `{', '.join(str(item) for item in summary['approved_media_ids'])}`",
        f"- Low-confidence excluded IDs: `{', '.join(str(item) for item in summary['low_confidence_excluded_media_ids'])}`",
        f"- Mode/status: `{summary['status']}`",
        f"- Source of truth: `{summary['source_of_truth']}`",
        "",
        "## Closeout Safety Gates",
        "",
        "- Manual validation is bound to exact SauceNAO/Danbooru result IDs before any write plan is treated as validated.",
        "- Approved result identities: `2687 -> 7695035`, `2670 -> 9366672`.",
        "- `db_write_allowed` is enforced by the persistence service; C1 promotes only approved validated plans to writable after phase gates pass.",
        "- Live rerun details and metadata extraction details must match on provider/result/source identity before metadata is combined.",
        "- Nested plan identity validation requires plan/provider_query/source_match media and provider identity to agree before DB writes.",
        "- Duplicate requested media IDs are rejected before plan build, backup, or apply.",
        "- Apply mode verifies post-write gates and idempotency inside the transaction before commit.",
        "- Apply mode writes audit artifacts before commit so DB success is contingent on report materialization.",
        "- Low-confidence and approved-evidence verification is scoped to C1 row identity or before/after deltas.",
        f"- New DB rows inserted during this closeout run: `{summary['closeout']['new_db_writes_during_closeout']}`",
        "",
        "## Lifecycle Classification",
        "",
        "- Persistence service code: durable provider/evidence infrastructure.",
        "- C1 runner: phase-scoped operational runner.",
        "- Reports: public report / handoff / roadmap update.",
        "- DB backup and local details: one-off local ignored artifacts.",
        "",
        "## DB Identity",
        "",
        f"- VIOLET_ENV: `{summary['db_identity']['violet_env']}`",
        f"- Configured/current DB: `{summary['db_identity']['configured_db']}` / `{summary['db_identity']['current_database']}`",
        f"- DB endpoint: `{summary['db_identity']['db_host']}:{summary['db_identity']['db_port']}`",
        f"- DB user: `{summary['db_identity']['db_user']}`",
        f"- Storage root basename: `{summary['db_identity']['storage_root_basename']}`",
        "",
        "## Backup",
        "",
        "- Backup created: " + ("yes" if backup else "no (dry-run only)"),
    ]
    if backup:
        lines.extend(
            [
                f"- Backup basename: `{backup['basename']}`",
                f"- Backup bytes: `{backup['bytes']}`",
                f"- Backup format: `{backup['format']}`",
                f"- TOC verified: `{backup['toc_verified']}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Write Results",
            "",
            "| Table | Planned | Inserted | Existing | Skipped |",
            "| --- | ---: | ---: | ---: | ---: |",
            f"| ProviderCache | {counts['ProviderCache']['planned']} | {counts['ProviderCache']['inserted']} | {counts['ProviderCache']['existing']} | {counts['ProviderCache']['skipped']} |",
            f"| EntityEvidence | {counts['EntityEvidence']['planned']} | {counts['EntityEvidence']['inserted']} | {counts['EntityEvidence']['existing']} | {counts['EntityEvidence']['skipped']} |",
            f"| MediaEntityCandidate | {counts['MediaEntityCandidate']['planned']} | {counts['MediaEntityCandidate']['inserted']} | {counts['MediaEntityCandidate']['existing']} | {counts['MediaEntityCandidate']['skipped']} |",
            "",
            f"- ProviderCache written: `{summary['provider_cache_written']}`",
            f"- EntityEvidence written: `{summary['entity_evidence_written']}`",
            f"- MediaEntityCandidate written: `{summary['media_entity_candidate_written']}`",
            f"- MediaEntityCandidate deferred: `{summary['media_entity_candidate_deferred']}`",
            "",
            "## Per-media Outcome",
            "",
            "| media_id | result_id | score | source_host | artist | work | character | localization |",
            "| ---: | --- | ---: | --- | --- | --- | --- | --- |",
        ]
    )
    for media_id, item in summary["per_media"].items():
        if item.get("status") == "blocked":
            lines.append(f"| {media_id} | blocked |  |  |  | {item.get('blocked_reason')} |  |  |")
        else:
            lines.append(
                f"| {media_id} | {item['result_id']} | {item['score']} | {item['source_host']} | "
                f"{', '.join(item['artist'])} | {', '.join(item['work'])} | {', '.join(item['character'])} | "
                f"{item['localization_status']} |"
            )
    verification = summary["post_write_verification"]
    lines.extend(
        [
            "",
            "## Post-write Verification",
            "",
            f"- ProviderCache approved count: `{verification['provider_cache_approved_count']}`",
            f"- EntityEvidence approved count: `{verification['entity_evidence_approved_count']}`",
            f"- MediaEntityCandidate C1 count: `{verification['media_entity_candidate_c1_count']}`",
            f"- ProviderCache unrelated existing ignored: `{verification['provider_cache_unrelated_existing_ignored']}`",
            f"- EntityEvidence unrelated existing ignored: `{verification['entity_evidence_unrelated_existing_ignored']}`",
            f"- MediaEntityCandidate unrelated existing ignored: `{verification['media_entity_candidates_unrelated_existing_ignored']}`",
            f"- Confirmed assignment count for approved media: `{verification['confirmed_assignment_count_for_approved']}`",
            f"- Verification success: `{verification['success']}`",
            f"- Failure codes: `{', '.join(verification['failure_codes']) if verification['failure_codes'] else 'none'}`",
            f"- Entity count unchanged: `{verification['entity_count_unchanged']}`",
            f"- TagTranslation count unchanged: `{verification['tag_translation_count_unchanged']}`",
            f"- media_tags for approved unchanged: `{verification['media_tags_for_approved_unchanged']}`",
            f"- Low-confidence positive evidence inserted by C1: `{verification['low_confidence_positive_evidence_inserted_by_c1']}`",
            f"- Low-confidence candidates inserted by C1: `{verification['low_confidence_candidates_inserted_by_c1']}`",
            f"- Entity count before/after: `{summary['db_state_before']['entity_count']}` / `{summary['db_state_after']['entity_count']}`",
            f"- TagTranslation count before/after: `{summary['db_state_before']['tag_translation_count']}` / `{summary['db_state_after']['tag_translation_count']}`",
            f"- media_tags for approved before/after: `{summary['db_state_before']['media_tags_for_approved']}` / `{summary['db_state_after']['media_tags_for_approved']}`",
            "",
            "## Idempotency Verification",
            "",
        ]
    )
    idempotency = summary.get("idempotency_verification")
    if idempotency:
        lines.extend(
            [
                f"- Status: `{idempotency['status']}`",
                f"- Check ran: `{idempotency['idempotency_check_ran']}`",
                f"- Success: `{idempotency['success']}`",
                f"- Failure codes: `{', '.join(idempotency['failure_codes']) if idempotency['failure_codes'] else 'none'}`",
                f"- ProviderCache existing count: `{idempotency['counts']['ProviderCache']['existing']}`",
                f"- EntityEvidence existing count: `{idempotency['counts']['EntityEvidence']['existing']}`",
                f"- MediaEntityCandidate existing count: `{idempotency['counts']['MediaEntityCandidate']['existing']}`",
                f"- Would insert counts: ProviderCache `{idempotency['would_insert_provider_cache']}`, EntityEvidence `{idempotency['would_insert_entity_evidence']}`, MediaEntityCandidate `{idempotency['would_insert_media_entity_candidate']}`",
            ]
        )
    else:
        lines.append("- Not applicable for dry-run mode.")
    lines.extend(
        [
            "",
            "## Low-confidence Exclusion",
            "",
            "`2690`, `2654`, and `2647` were excluded from positive persistence. No positive EntityEvidence or MediaEntityCandidate rows are written for them in C1.",
            "",
            "## Deferred Hardening",
            "",
            "- Pre-existing candidate conflict dry-run detection.",
            "- Non-suggested candidate decision preservation on rerun.",
            "- Dry-run post-write count semantics.",
            "- ProviderCache query-scoped payload redesign for duplicate images.",
            "- Broader candidate lifecycle hardening.",
            "- Caller-owned transaction rollback policy for future service callers.",
            "",
            "## Rollback",
            "",
            "Prefer restoring from the local ignored backup when a full rollback is needed. The full backup path is recorded only in local details.",
            "",
            "Targeted C1 delete SQL:",
            "",
            "```sql",
            summary["rollback"]["delete_sql_for_c1_rows"],
            "```",
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
            "## Next Step",
            "",
            "Recommended next step: review and merge this C1 persistence PR if reviewer finds no current-stage DB correctness, provenance, privacy, confirmed-assignment, or report-truthfulness issue. After merge, choose D0 second-provider scouting or a bounded B2 sample pilot.",
            "",
        ]
    )
    return "\n".join(lines)


def build_local_details(
    *,
    mode: str,
    backup: Mapping[str, Any] | None,
    dry_summary: Mapping[str, Any],
    persistence: Mapping[str, Any],
    post_write_verification: Mapping[str, Any] | None,
    idempotency_summary: Mapping[str, Any] | None,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    plans: list[Any],
    report_json: Path,
    report_md: Path,
    local_details_json: Path,
    metadata_details_json: Path,
) -> dict[str, Any]:
    return {
        "phase": PHASE,
        "mode": mode,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "public_summary_path": str(report_json),
        "public_report_path": str(report_md),
        "backup": backup,
        "dry_run": dry_summary,
        "persistence": persistence,
        "post_write_verification": post_write_verification,
        "idempotency_verification": idempotency_summary,
        "db_state_before": before,
        "db_state_after": after,
        "rollback_sql": build_rollback_sql(_plan_query_hashes(plans)),
        "source_artifacts": {
            "live_details": str(local_details_json),
            "metadata_details": str(metadata_details_json),
        },
    }


def write_audit_artifacts(
    *,
    report_json: Path,
    report_md: Path,
    details_json: Path,
    public_summary: Mapping[str, Any],
    local_details: Mapping[str, Any],
) -> None:
    write_json(report_json, public_summary)
    write_text(report_md, render_markdown(public_summary))
    write_json(details_json, local_details)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--media-ids", nargs="+", type=int, default=list(APPROVED_MEDIA_IDS))
    parser.add_argument("--report-md", type=Path, default=DEFAULT_REPORT_MD)
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--local-details-json", type=Path, default=DEFAULT_LIVE_DETAILS)
    parser.add_argument("--metadata-details-json", type=Path, default=DEFAULT_METADATA_DETAILS)
    parser.add_argument("--details-json", type=Path, default=DEFAULT_LOCAL_DETAILS)
    parser.add_argument("--db-backup-file", type=Path)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    mode = "apply" if args.apply else "dry_run"
    try:
        live_details = load_json(args.local_details_json)
        metadata_details = load_json(args.metadata_details_json)
        validate_local_artifact_flags(live_details, metadata_details)
        plans = build_phase44c1_plans(
            live_details=live_details,
            metadata_details=metadata_details,
            media_ids=args.media_ids,
        )
        settings, engine, identity = load_settings_and_engine()
        SessionLocal = sessionmaker(bind=engine)
        backup = None
        post_write_verification = None
        public_summary = None
        local_details = None
        audit_artifacts_written = False
        with SessionLocal() as db:
            low_qhashes = low_confidence_query_hashes(live_details)
            before = collect_db_state(db, plans=plans, low_confidence_query_hashes=low_qhashes)
            after = before
            ensure_media_rows_present(before, len(plans))

            dry_summary = persist_provider_evidence_plans(
                db,
                plans,
                apply=False,
                options=EvidencePersistenceOptions(write_candidates=True, strict=False if args.apply else args.strict),
            )

            blocked_status = None
            idempotency_summary = None
            if args.apply:
                if dry_summary.get("success") is not True:
                    persistence = dry_summary
                    blocked_status = "blocked_before_apply"
                else:
                    backup_path = args.db_backup_file or (
                        Path(".local_manifests")
                        / f"phase-4.4c1-db-backup-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.dump"
                    )
                    backup = create_pg_dump_backup(settings, backup_path)
                    try:
                        apply_result = apply_plans_with_pre_commit_gates(
                            db,
                            plans=plans,
                            db_before=before,
                            low_confidence_query_hashes=low_qhashes,
                        )
                        persistence = apply_result["persistence"]
                        after = apply_result["db_after"]
                        post_write_verification = apply_result["post_write_verification"]
                        idempotency_summary = apply_result["idempotency_summary"]
                        public_summary = build_public_summary(
                            mode=mode,
                            identity=identity,
                            plans=plans,
                            persistence=persistence,
                            db_before=before,
                            db_after=after,
                            backup=backup,
                            idempotency_summary=idempotency_summary,
                            post_write_verification=post_write_verification,
                            blocked_status=blocked_status,
                        )
                        local_details = build_local_details(
                            mode=mode,
                            backup=backup,
                            dry_summary=dry_summary,
                            persistence=persistence,
                            post_write_verification=post_write_verification,
                            idempotency_summary=idempotency_summary,
                            before=before,
                            after=after,
                            plans=plans,
                            report_json=args.report_json,
                            report_md=args.report_md,
                            local_details_json=args.local_details_json,
                            metadata_details_json=args.metadata_details_json,
                        )
                        write_audit_artifacts(
                            report_json=args.report_json,
                            report_md=args.report_md,
                            details_json=args.details_json,
                            public_summary=public_summary,
                            local_details=local_details,
                        )
                        audit_artifacts_written = True
                        db.commit()
                    except Exception as exc:
                        db.rollback()
                        after = collect_db_state(db, plans=plans, low_confidence_query_hashes=low_qhashes)
                        persistence = deepcopy(dry_summary)
                        persistence["success"] = False
                        persistence["apply_error"] = {
                            "code": getattr(exc, "code", exc.__class__.__name__),
                            "message": str(exc),
                        }
                        blocked_status = "blocked_apply_failed"
                        public_summary = None
                        local_details = None
                        audit_artifacts_written = False
            else:
                persistence = dry_summary
                after = collect_db_state(db, plans=plans, low_confidence_query_hashes=low_qhashes)

        if public_summary is None:
            public_summary = build_public_summary(
                mode=mode,
                identity=identity,
                plans=plans,
                persistence=persistence,
                db_before=before,
                db_after=after,
                backup=backup,
                idempotency_summary=idempotency_summary,
                post_write_verification=post_write_verification,
                blocked_status=blocked_status,
            )
        if local_details is None:
            local_details = build_local_details(
                mode=mode,
                backup=backup,
                dry_summary=dry_summary,
                persistence=persistence,
                post_write_verification=post_write_verification,
                idempotency_summary=idempotency_summary,
                before=before,
                after=after,
                plans=plans,
                report_json=args.report_json,
                report_md=args.report_md,
                local_details_json=args.local_details_json,
                metadata_details_json=args.metadata_details_json,
            )
        if not audit_artifacts_written:
            write_audit_artifacts(
                report_json=args.report_json,
                report_md=args.report_md,
                details_json=args.details_json,
                public_summary=public_summary,
                local_details=local_details,
            )
        print(json.dumps({"status": public_summary["status"], "success": public_summary["success"]}, sort_keys=True))
        engine.dispose()
        return 0 if public_summary["success"] else 2
    except Exception as exc:
        code = exc.code if isinstance(exc, PhaseC1Error) else exc.__class__.__name__
        print(json.dumps({"status": "blocked", "success": False, "code": code, "error": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
