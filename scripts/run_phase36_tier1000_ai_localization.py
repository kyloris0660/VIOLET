"""Controlled Phase 3.6 Tier-1000 AI tagging and localization runner.

This script intentionally avoids the broad "all untagged media" paths used by
the generic admin UI.  It only targets media imported by Phase 3.5 through the
privacy-safe source label, then records public JSON summaries with secrets and
local paths redacted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import urlparse

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session


REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


SOURCE_LABEL = "violet:tier1000:phase3.5"
CONFIRM_PHRASE = "PHASE36_TIER1000_AI_LOCALIZATION"
AI_SOURCE = "ai_wd"
PUBLIC_PATH_REDACTION = "<redacted_path>"
SECRET_REDACTION = "<redacted_secret>"
LOCALIZABLE_CATEGORIES = ("general", "meta")
PROPER_NOUN_CATEGORIES = ("character", "copyright", "artist")
ACTIVE_AI_JOB_STATUSES = ("pending", "running", "cancelling")


class Phase36RunFailed(RuntimeError):
    """A controlled Phase 3.6 command wrote its report but must exit nonzero."""

    def __init__(self, message: str, payload: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.payload = payload


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def category_value(category: Any) -> str:
    if hasattr(category, "value"):
        return str(category.value)
    text = str(category)
    return text.split(".", 1)[-1] if "." in text else text


def sanitize_public_text(value: str) -> str:
    """Redact local paths and secrets from public report strings."""
    text = str(value)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._\-]+", f"Bearer {SECRET_REDACTION}", text)
    text = re.sub(r"(sk-|key-)[A-Za-z0-9_\-]{8,}", r"\1***", text)
    text = re.sub(r"(?i)(?<![A-Za-z])[A-Z]:[\\/][^\s\"'<>|]+", PUBLIC_PATH_REDACTION, text)
    text = re.sub(r"\\\\[^\\/\s]+\\[^\s\"'<>|]+", PUBLIC_PATH_REDACTION, text)
    text = re.sub(
        r"(?<!https:)(?<!http:)/(?:mnt|Volumes|workspace|home|Users|tmp|var|opt)(?:/[^\s\"'<>|]+)+",
        PUBLIC_PATH_REDACTION,
        text,
    )
    return text


def sanitize_public_obj(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_public_text(value)
    if isinstance(value, list):
        return [sanitize_public_obj(item) for item in value]
    if isinstance(value, dict):
        return {str(key): sanitize_public_obj(item) for key, item in value.items()}
    return value


def write_public_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_payload = sanitize_public_obj(payload)
    path.write_text(json.dumps(safe_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def redacted_db_url(settings: Any) -> str:
    return (
        f"postgresql://{settings.DB_USER}:***@"
        f"{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
    )


def safe_url_host(url: str) -> str:
    if not url:
        return ""
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def proxy_presence() -> Dict[str, bool]:
    return {
        "HTTPS_PROXY": bool(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")),
        "HTTP_PROXY": bool(os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")),
    }


def llm_config_summary(settings: Any) -> Dict[str, Any]:
    return {
        "enabled": bool(settings.TAG_TRANSLATION_LLM_ENABLED),
        "provider": settings.TAG_TRANSLATION_LLM_PROVIDER,
        "base_url_host": safe_url_host(settings.TAG_TRANSLATION_LLM_BASE_URL),
        "model": settings.TAG_TRANSLATION_LLM_MODEL,
        "api_key_configured": bool(settings.TAG_TRANSLATION_LLM_API_KEY),
        "fallback_base_url_host": safe_url_host(settings.TAG_TRANSLATION_LLM_FALLBACK_BASE_URL),
        "fallback_model": settings.TAG_TRANSLATION_LLM_FALLBACK_MODEL,
        "fallback_api_key_configured": bool(settings.TAG_TRANSLATION_LLM_FALLBACK_API_KEY),
        "proxy_present": proxy_presence(),
    }


def load_app_context() -> Dict[str, Any]:
    from app import database as database_mod
    from app.config import settings

    if database_mod.SessionLocal is None:
        database_mod.init_engine()
    if database_mod.SessionLocal is None:
        raise RuntimeError("Database is not initialized")
    return {"database": database_mod, "settings": settings}


def new_session() -> Session:
    ctx = load_app_context()
    return ctx["database"].SessionLocal()


def tag_count_by_category(db: Session) -> Dict[str, int]:
    from app.models import Tag

    rows = db.query(Tag.category, func.count(Tag.id)).group_by(Tag.category).all()
    return {category_value(category): int(count) for category, count in rows}


def target_media_count(db: Session, source_label: str) -> int:
    from app.models import Media

    return int(db.query(func.count(Media.id)).filter(Media.source == source_label).scalar() or 0)


def count_target_ai_associations(db: Session, source_label: str, *, suggestions: Optional[bool] = None) -> int:
    from app.models import Media, blombooru_media_tags

    query = (
        db.query(func.count())
        .select_from(blombooru_media_tags)
        .join(Media, Media.id == blombooru_media_tags.c.media_id)
        .filter(Media.source == source_label)
        .filter(blombooru_media_tags.c.source == AI_SOURCE)
    )
    if suggestions is not None:
        query = query.filter(blombooru_media_tags.c.is_suggestion.is_(suggestions))
    return int(query.scalar() or 0)


def count_target_media_with_ai_tags(db: Session, source_label: str) -> int:
    from app.models import Media, blombooru_media_tags

    return int(
        db.query(func.count(func.distinct(blombooru_media_tags.c.media_id)))
        .select_from(blombooru_media_tags)
        .join(Media, Media.id == blombooru_media_tags.c.media_id)
        .filter(Media.source == source_label)
        .filter(blombooru_media_tags.c.source == AI_SOURCE)
        .scalar()
        or 0
    )


def count_classification_jobs(db: Session) -> int:
    from app.models import ClassificationJob

    return int(db.query(func.count(ClassificationJob.id)).scalar() or 0)


def count_translation_jobs(db: Session) -> int:
    from app.models import TagTranslationJob

    return int(db.query(func.count(TagTranslationJob.id)).scalar() or 0)


def find_active_ai_jobs(db: Session) -> List[Dict[str, Any]]:
    """Return DB-backed active AI jobs that could collide with Phase 3.6 chunks."""
    from app.models import AITagJob

    rows = (
        db.query(AITagJob)
        .filter(AITagJob.status.in_(ACTIVE_AI_JOB_STATUSES))
        .order_by(AITagJob.id.asc())
        .all()
    )
    return [
        {
            "id": int(job.id),
            "status": str(job.status),
            "trigger_source": str(job.trigger_source),
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
        }
        for job in rows
    ]


def ensure_no_db_active_ai_jobs(db: Session) -> None:
    active_jobs = find_active_ai_jobs(db)
    if active_jobs:
        safe_summary = json.dumps(sanitize_public_obj(active_jobs), ensure_ascii=False)
        raise RuntimeError(f"Active AI tagging jobs already exist in DB: {safe_summary}")


def apply_failure_status(payload: Dict[str, Any], *, status: str, error: str) -> Dict[str, Any]:
    payload["success"] = False
    payload["status"] = status
    payload.setdefault("errors", []).append(sanitize_public_text(error))
    payload["finished_at"] = utc_now()
    return payload


def assert_no_forbidden_ai_side_effects(payload: Dict[str, Any]) -> None:
    safety = payload.get("safety", {})
    violations = {}
    if int(safety.get("content_classification_jobs_delta", 0) or 0) != 0:
        violations["content_classification_jobs_delta"] = safety["content_classification_jobs_delta"]
    if int(safety.get("translation_jobs_delta_during_ai", 0) or 0) != 0:
        violations["translation_jobs_delta_during_ai"] = safety["translation_jobs_delta_during_ai"]
    if violations:
        safety["phase_isolation_passed"] = False
        payload["safety"] = safety
        apply_failure_status(
            payload,
            status="failed_phase_isolation_violation",
            error=f"Forbidden Phase 3.6 side-effect jobs were created: {violations}",
        )
        raise Phase36RunFailed("Forbidden side-effect jobs were created during AI tagging", payload)


def effective_localization_limit(requested_max_items: int, configured_batch_max: int) -> int:
    if requested_max_items <= 0:
        raise RuntimeError("--max-items must be a positive integer")
    if configured_batch_max <= 0:
        raise RuntimeError("TAG_TRANSLATION_BATCH_MAX_ITEMS must be a positive integer")
    return min(int(requested_max_items), int(configured_batch_max))


def build_ai_chunk_failure_payload(
    *,
    started_at: str,
    source_label: str,
    common: Dict[str, Any],
    ai_gates: Dict[str, Any],
    model_status: Dict[str, Any],
    expected_media_count: int,
    target_ids: Sequence[int],
    chunks: Sequence[Sequence[int]],
    chunk_size: int,
    before: Dict[str, Any],
    jobs: List[Dict[str, Any]],
    failed_job: Dict[str, Any],
    totals: Dict[str, int],
    db: Session,
    lang: str,
    error: str,
) -> Dict[str, Any]:
    safe_error = sanitize_public_text(error)
    warnings: List[str] = []
    after: Optional[Dict[str, Any]] = None
    delta: Optional[Dict[str, int]] = None
    safety: Dict[str, Any] = {
        "content_classification_jobs_delta": None,
        "translation_jobs_delta_during_ai": None,
        "entity_alias_resolver": "not_run",
        "source_staging_mutation": False,
        "phase_isolation_passed": False,
    }
    try:
        after = collect_baseline(db, source_label, lang=lang)
        delta = metric_delta(before, after)
        safety["content_classification_jobs_delta"] = (
            after["classification_jobs"] - before["classification_jobs"]
        )
        safety["translation_jobs_delta_during_ai"] = after["translation_jobs"] - before["translation_jobs"]
    except Exception as exc:
        warnings.append(f"post-failure baseline collection failed: {sanitize_public_text(str(exc))}")

    return {
        "mode": "ai_tagging_execute",
        "started_at": started_at,
        "finished_at": utc_now(),
        "source_label": source_label,
        "database": common["database"],
        "storage": {
            "storage_root_label": "app_storage",
            "paths_redacted": True,
            "source_staging_mutation": False,
        },
        "backup": common["backup"],
        "gates": {**common["gates"], **ai_gates},
        "model_status": model_status,
        "target": {
            "expected_media_count": expected_media_count,
            "selected_media_count": len(target_ids),
            "chunk_size": chunk_size,
            "chunk_count": len(chunks),
        },
        "before": before,
        "after": after,
        "delta": delta,
        "ai_tagging": {
            **totals,
            "job_ids": [job["id"] for job in jobs],
            "completed_jobs": [job for job in jobs if job.get("status") == "completed" and not job.get("failed")],
            "failed_job": failed_job,
            "jobs": jobs,
        },
        "safety": safety,
        "success": False,
        "status": "failed_ai_chunk",
        "errors": [safe_error],
        "warnings": warnings,
    }


def mark_translation_job_failed(
    db: Session,
    job: Any,
    *,
    error: str,
    total_candidates: int,
    processed: int = 0,
) -> Any:
    safe_error = sanitize_public_text(error)[:2000]
    try:
        db.rollback()
    except Exception:
        pass
    job = db.merge(job)
    job.status = "failed"
    job.processed = max(int(processed or 0), 0)
    job.failed = max(int(total_candidates) - job.processed, int(total_candidates) if processed == 0 else 0)
    job.last_error = safe_error
    job.error_message = safe_error
    job.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    return job


def count_missing_by_categories(
    db: Session,
    source_label: str,
    categories: Sequence[str],
    lang: str = "zh-CN",
) -> int:
    return len(select_localization_candidates(db, source_label, categories, lang=lang, limit=None))


def collect_baseline(db: Session, source_label: str, lang: str = "zh-CN") -> Dict[str, Any]:
    from app.models import Tag, TagTranslation

    return {
        "source_label": source_label,
        "target_media_count": target_media_count(db, source_label),
        "target_media_with_ai_tags": count_target_media_with_ai_tags(db, source_label),
        "target_ai_confirmed_associations": count_target_ai_associations(db, source_label, suggestions=False),
        "target_ai_suggestion_associations": count_target_ai_associations(db, source_label, suggestions=True),
        "target_ai_associations": count_target_ai_associations(db, source_label),
        "total_tag_count": int(db.query(func.count(Tag.id)).scalar() or 0),
        "tag_count_by_category": tag_count_by_category(db),
        "translation_count": int(
            db.query(func.count(TagTranslation.id))
            .filter(TagTranslation.language == lang, TagTranslation.status != "rejected")
            .scalar()
            or 0
        ),
        "target_missing_visual_translations": count_missing_by_categories(db, source_label, LOCALIZABLE_CATEGORIES, lang),
        "target_missing_proper_noun_translations": count_missing_by_categories(db, source_label, PROPER_NOUN_CATEGORIES, lang),
        "classification_jobs": count_classification_jobs(db),
        "translation_jobs": count_translation_jobs(db),
    }


def metric_delta(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, int]:
    keys = [
        "target_media_with_ai_tags",
        "target_ai_confirmed_associations",
        "target_ai_suggestion_associations",
        "target_ai_associations",
        "total_tag_count",
        "translation_count",
        "classification_jobs",
        "translation_jobs",
    ]
    return {key: int(after.get(key, 0)) - int(before.get(key, 0)) for key in keys}


def select_target_media_ids(
    db: Session,
    source_label: str,
    *,
    only_without_ai_tags: bool = True,
    limit: Optional[int] = None,
) -> List[int]:
    from app.models import Media, blombooru_media_tags

    query = db.query(Media.id).filter(Media.source == source_label)
    if only_without_ai_tags:
        ai_tagged = (
            db.query(blombooru_media_tags.c.media_id)
            .filter(blombooru_media_tags.c.source == AI_SOURCE)
            .distinct()
            .subquery()
        )
        query = query.filter(~Media.id.in_(select(ai_tagged.c.media_id)))
    query = query.order_by(Media.id.asc())
    if limit is not None:
        query = query.limit(limit)
    return [int(row[0]) for row in query.all()]


def chunked(ids: Sequence[int], chunk_size: int) -> List[List[int]]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    return [list(ids[i : i + chunk_size]) for i in range(0, len(ids), chunk_size)]


def validate_backup_file(path: Optional[Path]) -> Dict[str, Any]:
    if path is None:
        raise RuntimeError("--db-backup-file is required for write modes")
    if not path.exists():
        raise RuntimeError("--db-backup-file does not exist")
    size = path.stat().st_size
    if size <= 0:
        raise RuntimeError("--db-backup-file is empty")
    return {"file_name": path.name, "bytes": size, "path_redacted": True}


def validate_common_write_gates(
    *,
    db: Session,
    settings: Any,
    source_label: str,
    expected_media_count: int,
    confirm_phrase: str,
    backup_file: Optional[Path],
) -> Dict[str, Any]:
    if confirm_phrase != CONFIRM_PHRASE:
        raise RuntimeError(f"--confirm-phase36 must be exactly {CONFIRM_PHRASE}")
    backup = validate_backup_file(backup_file)
    count = target_media_count(db, source_label)
    if count != expected_media_count:
        raise RuntimeError(
            f"Target media count mismatch for {source_label}: expected {expected_media_count}, got {count}"
        )
    if settings.DB_NAME != "blombooru":
        raise RuntimeError(f"Refusing write mode against unexpected DB {settings.DB_NAME!r}")
    if settings.IS_TEST_ENV:
        raise RuntimeError("Refusing Phase 3.6 real write mode with VIOLET_ENV=test")
    if settings.CONTENT_CLASSIFICATION_ENABLED or settings.CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT:
        raise RuntimeError("Content classification must be disabled for Phase 3.6")
    if settings.ENTITY_ALIAS_RESOLVER_ENABLED:
        raise RuntimeError("Entity Alias Resolver must be disabled for Phase 3.6")
    if settings.TAG_TRANSLATION_BG_ENABLED or settings.TAG_TRANSLATION_AUTO_ENABLED:
        raise RuntimeError("Background/auto tag translation must be disabled for Phase 3.6")
    if settings.AI_TAGGING_AUTO_LOCALIZATION:
        raise RuntimeError("AI_TAGGING_AUTO_LOCALIZATION must be false for Phase 3.6")
    return {
        "backup": backup,
        "database": {
            "violet_env": settings.VIOLET_ENV,
            "db_name": settings.DB_NAME,
            "database_url_safe": redacted_db_url(settings),
        },
        "gates": {
            "target_media_count": count,
            "content_classification_disabled": True,
            "entity_alias_resolver_disabled": True,
            "tag_translation_background_disabled": True,
            "tag_translation_auto_disabled": True,
            "ai_auto_localization_disabled": True,
            "source_staging_mutation": False,
        },
    }


def validate_ai_gates(settings: Any) -> Dict[str, Any]:
    if not settings.AI_TAGGING_ENABLED:
        raise RuntimeError("AI_TAGGING_ENABLED must be true for AI tagging execution")
    if settings.TAG_TRANSLATION_LLM_ENABLED:
        raise RuntimeError("TAG_TRANSLATION_LLM_ENABLED must be false during AI tagging execution")
    return {
        "ai_tagging_enabled": True,
        "llm_disabled_during_ai_tagging": True,
        "batch_max_items": settings.AI_TAGGING_BATCH_MAX_ITEMS,
        "model_name": settings.AI_MODEL_NAME,
        "thresholds": {
            "general": settings.AI_GENERAL_THRESHOLD,
            "character": settings.AI_CHARACTER_THRESHOLD,
            "rating": settings.AI_RATING_THRESHOLD,
            "suggestion": settings.AI_SUGGESTION_THRESHOLD,
        },
    }


def validate_localization_gates(settings: Any) -> Dict[str, Any]:
    if not settings.TAG_TRANSLATION_LLM_ENABLED:
        raise RuntimeError("TAG_TRANSLATION_LLM_ENABLED must be true for controlled localization")
    return {
        "llm_enabled_for_controlled_localization": True,
        "llm": llm_config_summary(settings),
    }


def run_baseline(args: argparse.Namespace) -> Dict[str, Any]:
    ctx = load_app_context()
    settings = ctx["settings"]
    db = ctx["database"].SessionLocal()
    try:
        payload = {
            "mode": "baseline",
            "started_at": utc_now(),
            "source_label": args.source_label,
            "database": {
                "violet_env": settings.VIOLET_ENV,
                "db_name": settings.DB_NAME,
                "database_url_safe": redacted_db_url(settings),
            },
            "storage": {
                "storage_root_label": "app_storage",
                "paths_redacted": True,
            },
            "baseline": collect_baseline(db, args.source_label, lang=args.lang),
            "finished_at": utc_now(),
        }
        if args.report_json:
            write_public_json(Path(args.report_json), payload)
        return payload
    finally:
        db.close()


def run_ai_tagging_controlled(args: argparse.Namespace) -> Dict[str, Any]:
    from app.services.ai_tagging_job_service import create_ai_tag_job, is_ai_job_active, run_ai_tag_job
    from app.services.ai_tagging_service import check_model_status
    from app.models import AITagJob

    ctx = load_app_context()
    settings = ctx["settings"]
    db = ctx["database"].SessionLocal()
    started_at = utc_now()
    try:
        common = validate_common_write_gates(
            db=db,
            settings=settings,
            source_label=args.source_label,
            expected_media_count=args.expected_media_count,
            confirm_phrase=args.confirm_phase36,
            backup_file=Path(args.db_backup_file) if args.db_backup_file else None,
        )
        ai_gates = validate_ai_gates(settings)
        ensure_no_db_active_ai_jobs(db)
        if is_ai_job_active():
            raise RuntimeError("An AI tagging job is already active")

        before = collect_baseline(db, args.source_label, lang=args.lang)
        target_ids = select_target_media_ids(
            db,
            args.source_label,
            only_without_ai_tags=True,
            limit=args.limit,
        )
        if not target_ids:
            raise RuntimeError("No untagged Phase 3.5 media IDs selected; refusing empty-scope job")
        hard_limit = int(settings.AI_TAGGING_BATCH_MAX_ITEMS)
        chunk_size = min(args.chunk_size, hard_limit)
        chunks = chunked(target_ids, chunk_size)
        if any(not chunk for chunk in chunks):
            raise RuntimeError("Internal error: empty media ID chunk")

        model_status = check_model_status()
        if not model_status.get("enabled"):
            raise RuntimeError("AI model status reports AI tagging disabled")
        if model_status.get("error"):
            raise RuntimeError(f"AI model status error: {model_status['error']}")

        jobs: List[Dict[str, Any]] = []
        totals = {
            "processed": 0,
            "failed": 0,
            "tags_added": 0,
            "suggestions_added": 0,
            "skipped_locked": 0,
            "ignored_low_confidence": 0,
        }

        for index, chunk in enumerate(chunks, start=1):
            job = create_ai_tag_job(
                db,
                media_ids=chunk,
                max_items=len(chunk),
                dry_run=False,
                only_without_ai_tags=True,
                force_suggestions=False,
                trigger_source="phase3.6",
            )
            run_ai_tag_job(job.id)
            db.refresh(job)
            persisted = db.get(AITagJob, job.id)
            if persisted is None:
                raise RuntimeError(f"AI tag job {job.id} disappeared")
            job_entry = {
                "id": persisted.id,
                "chunk_index": index,
                "requested_media_count": len(chunk),
                "status": persisted.status,
                "processed": persisted.processed,
                "failed": persisted.failed,
                "tags_added": persisted.tags_added,
                "suggestions_added": persisted.suggestions_added,
                "skipped_locked": persisted.skipped_locked,
                "ignored_low_confidence": persisted.ignored_low_confidence,
                "localization_status": persisted.localization_status,
                "error_message": persisted.error_message,
                "started_at": persisted.started_at.isoformat() if persisted.started_at else None,
                "finished_at": persisted.finished_at.isoformat() if persisted.finished_at else None,
            }
            jobs.append(job_entry)
            if persisted.status != "completed" or persisted.failed:
                failure_payload = build_ai_chunk_failure_payload(
                    started_at=started_at,
                    source_label=args.source_label,
                    common=common,
                    ai_gates=ai_gates,
                    model_status=model_status,
                    expected_media_count=args.expected_media_count,
                    target_ids=target_ids,
                    chunks=chunks,
                    chunk_size=chunk_size,
                    before=before,
                    jobs=jobs,
                    failed_job=job_entry,
                    totals=totals,
                    db=db,
                    lang=args.lang,
                    error=f"AI tag job {persisted.id} did not complete cleanly: {job_entry}",
                )
                if args.report_json:
                    write_public_json(Path(args.report_json), failure_payload)
                raise Phase36RunFailed(
                    f"AI tag job {persisted.id} did not complete cleanly",
                    failure_payload,
                )
            for key in totals:
                totals[key] += int(getattr(persisted, key) or 0)
            sys.stderr.write(
                f"completed_ai_chunk={index}/{len(chunks)} "
                f"job_id={persisted.id} processed={persisted.processed} "
                f"tags_added={persisted.tags_added} suggestions_added={persisted.suggestions_added}\n"
            )
            sys.stderr.flush()

        after = collect_baseline(db, args.source_label, lang=args.lang)
        payload = {
            "mode": "ai_tagging_execute",
            "started_at": started_at,
            "finished_at": utc_now(),
            "source_label": args.source_label,
            "database": common["database"],
            "storage": {
                "storage_root_label": "app_storage",
                "paths_redacted": True,
                "source_staging_mutation": False,
            },
            "backup": common["backup"],
            "gates": {**common["gates"], **ai_gates},
            "model_status": model_status,
            "target": {
                "expected_media_count": args.expected_media_count,
                "selected_media_count": len(target_ids),
                "chunk_size": chunk_size,
                "chunk_count": len(chunks),
            },
            "before": before,
            "after": after,
            "delta": metric_delta(before, after),
            "ai_tagging": {
                **totals,
                "job_ids": [job["id"] for job in jobs],
                "jobs": jobs,
            },
            "safety": {
                "content_classification_jobs_delta": after["classification_jobs"] - before["classification_jobs"],
                "translation_jobs_delta_during_ai": after["translation_jobs"] - before["translation_jobs"],
                "entity_alias_resolver": "not_run",
                "source_staging_mutation": False,
                "phase_isolation_passed": True,
            },
            "success": True,
            "status": "completed",
            "errors": [],
            "warnings": [],
        }
        try:
            assert_no_forbidden_ai_side_effects(payload)
        except Phase36RunFailed as exc:
            if args.report_json:
                write_public_json(Path(args.report_json), exc.payload or payload)
            raise
        if args.report_json:
            write_public_json(Path(args.report_json), payload)
        return payload
    finally:
        db.close()


def _missing_translation_subquery(db: Session, lang: str):
    from app.models import TagTranslation

    return (
        db.query(TagTranslation.canonical_name)
        .filter(TagTranslation.language == lang)
        .filter(TagTranslation.status != "rejected")
        .subquery()
    )


def _static_translation_names() -> List[str]:
    from app.services.tag_localization_service import _load_static_dict

    return list(_load_static_dict()["tags"].keys())


def _category_enums(categories: Sequence[str]) -> List[Any]:
    from app.enums import TagCategoryEnum

    mapping = {item.value: item for item in TagCategoryEnum}
    return [mapping[name] for name in categories if name in mapping]


def select_localization_candidates(
    db: Session,
    source_label: str,
    categories: Sequence[str],
    *,
    lang: str = "zh-CN",
    limit: Optional[int] = 500,
) -> List[Dict[str, Any]]:
    from app.models import Media, Tag, blombooru_media_tags

    existing = _missing_translation_subquery(db, lang)
    static_names = _static_translation_names()
    query = (
        db.query(Tag)
        .join(blombooru_media_tags, Tag.id == blombooru_media_tags.c.tag_id)
        .join(Media, Media.id == blombooru_media_tags.c.media_id)
        .filter(Media.source == source_label)
        .filter(blombooru_media_tags.c.source == AI_SOURCE)
        .filter(Tag.category.in_(_category_enums(categories)))
        .filter(~Tag.name.in_(select(existing.c.canonical_name)))
        .distinct()
        .order_by(Tag.post_count.desc(), Tag.name.asc())
    )
    if static_names:
        query = query.filter(~Tag.name.in_(static_names))
    if limit is not None:
        query = query.limit(limit)
    return [
        {
            "tag_id": tag.id,
            "canonical_name": tag.name,
            "category": category_value(tag.category),
            "post_count": tag.post_count,
        }
        for tag in query.all()
    ]


def run_controlled_localization(args: argparse.Namespace) -> Dict[str, Any]:
    from app.models import TagTranslationJob
    from app.services.llm_translation_provider import get_llm_provider, _sanitize_error_message
    from app.services.tag_localization_service import upsert_translation
    from app.utils.search_parser import invalidate_translation_cache

    ctx = load_app_context()
    settings = ctx["settings"]
    db = ctx["database"].SessionLocal()
    started_at = utc_now()
    job: Optional[TagTranslationJob] = None
    try:
        common = validate_common_write_gates(
            db=db,
            settings=settings,
            source_label=args.source_label,
            expected_media_count=args.expected_media_count,
            confirm_phrase=args.confirm_phase36,
            backup_file=Path(args.db_backup_file) if args.db_backup_file else None,
        )
        localization_gates = validate_localization_gates(settings)
        before = collect_baseline(db, args.source_label, lang=args.lang)
        requested_max_items = int(args.max_items)
        configured_batch_max = int(settings.TAG_TRANSLATION_BATCH_MAX_ITEMS)
        effective_max_items = effective_localization_limit(requested_max_items, configured_batch_max)
        candidates = select_localization_candidates(
            db,
            args.source_label,
            LOCALIZABLE_CATEGORIES,
            lang=args.lang,
            limit=effective_max_items,
        )
        skipped_proper_nouns = len(
            select_localization_candidates(
                db,
                args.source_label,
                PROPER_NOUN_CATEGORIES,
                lang=args.lang,
                limit=None,
            )
        )

        provider = get_llm_provider()
        result: Dict[str, Any] = {
            "mode": "controlled_localization_execute",
            "started_at": started_at,
            "source_label": args.source_label,
            "database": common["database"],
            "storage": {
                "storage_root_label": "app_storage",
                "paths_redacted": True,
                "source_staging_mutation": False,
            },
            "backup": common["backup"],
            "gates": {**common["gates"], **localization_gates},
            "before": before,
            "candidates": len(candidates),
            "requested_max_items": requested_max_items,
            "effective_max_items": effective_max_items,
            "configured_batch_max": configured_batch_max,
            "candidates_selected": len(candidates),
            "localizable_categories": list(LOCALIZABLE_CATEGORIES),
            "proper_noun_categories_skipped": list(PROPER_NOUN_CATEGORIES),
            "skipped_proper_nouns": skipped_proper_nouns,
            "translated": 0,
            "failed": 0,
            "skipped": 0,
            "job_id": None,
            "provider_available": provider.is_available(),
            "success": True,
            "status": "running",
            "errors": [],
            "warnings": [],
        }

        if not candidates:
            result["status"] = "noop_no_candidates"
            result["after"] = collect_baseline(db, args.source_label, lang=args.lang)
            result["delta"] = metric_delta(before, result["after"])
            result["remaining_missing"] = result["after"]["target_missing_visual_translations"]
            result["finished_at"] = utc_now()
            if args.report_json:
                write_public_json(Path(args.report_json), result)
            return result

        if not provider.is_available():
            apply_failure_status(
                result,
                status="failed_provider_unavailable",
                error="LLM provider not available or not configured",
            )
            result["failed"] = len(candidates)
            result["after"] = collect_baseline(db, args.source_label, lang=args.lang)
            result["delta"] = metric_delta(before, result["after"])
            result["remaining_missing"] = result["after"]["target_missing_visual_translations"]
            if args.report_json:
                write_public_json(Path(args.report_json), result)
            raise Phase36RunFailed("LLM provider unavailable with localization candidates", result)

        job = TagTranslationJob(
            status="running",
            source="phase3.6",
            language=args.lang,
            category="general,meta",
            batch_size=min(len(candidates), effective_max_items),
            max_per_run=effective_max_items,
            processed=0,
            translated=0,
            failed=0,
            skipped=skipped_proper_nouns,
            remaining_before=len(candidates),
            started_at=datetime.now(timezone.utc),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        result["job_id"] = job.id

        tag_inputs = [
            {"name": item["canonical_name"], "category": item["category"]}
            for item in candidates
        ]
        try:
            translations = await_provider(provider, tag_inputs)
        except Exception as exc:
            safe_error = sanitize_public_text(_sanitize_error_message(str(exc)))[:2000]
            job = mark_translation_job_failed(
                db,
                job,
                error=safe_error,
                total_candidates=len(candidates),
                processed=0,
            )
            result["job_id"] = job.id
            result["failed"] = len(candidates)
            apply_failure_status(result, status="failed_provider_error", error=safe_error)
            result["after"] = collect_baseline(db, args.source_label, lang=args.lang)
            result["delta"] = metric_delta(before, result["after"])
            result["remaining_missing"] = result["after"]["target_missing_visual_translations"]
            if args.report_json:
                write_public_json(Path(args.report_json), result)
            raise Phase36RunFailed("LLM provider failed during controlled localization", result)

        candidate_by_name = {item["canonical_name"]: item for item in candidates}
        seen = set()
        try:
            for translation in translations:
                canonical = getattr(translation, "canonical_name", "")
                if canonical not in candidate_by_name:
                    result["skipped"] += 1
                    continue
                seen.add(canonical)
                item = candidate_by_name[canonical]
                saved = upsert_translation(
                    db,
                    canonical_name=canonical,
                    display_name=getattr(translation, "display_name_zh", ""),
                    lang=args.lang,
                    aliases=getattr(translation, "aliases_zh", []) or [],
                    category=item["category"],
                    source="llm",
                    status="translated",
                    confidence=getattr(translation, "confidence", None),
                    needs_review=bool(getattr(translation, "needs_review", False)),
                    provider=provider.get_provider_name(),
                )
                if saved is None:
                    result["skipped"] += 1
                else:
                    result["translated"] += 1

            result["failed"] = max(0, len(candidates) - len(seen))
            result["skipped"] += skipped_proper_nouns
            invalidate_translation_cache()
            after = collect_baseline(db, args.source_label, lang=args.lang)
            job.status = "completed" if result["failed"] == 0 else "failed"
            job.processed = len(candidates)
            job.translated = result["translated"]
            job.failed = result["failed"]
            job.skipped = result["skipped"]
            job.remaining_after = after["target_missing_visual_translations"]
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
        except Exception as exc:
            safe_error = sanitize_public_text(_sanitize_error_message(str(exc)))[:2000]
            job = mark_translation_job_failed(
                db,
                job,
                error=safe_error,
                total_candidates=len(candidates),
                processed=len(seen),
            )
            result["job_id"] = job.id
            result["failed"] = max(1, len(candidates) - len(seen))
            apply_failure_status(result, status="failed_save_or_finalize_error", error=safe_error)
            try:
                result["after"] = collect_baseline(db, args.source_label, lang=args.lang)
                result["delta"] = metric_delta(before, result["after"])
                result["remaining_missing"] = result["after"]["target_missing_visual_translations"]
            except Exception as audit_exc:
                result["warnings"].append(
                    f"post-failure baseline collection failed: {sanitize_public_text(str(audit_exc))}"
                )
            if args.report_json:
                write_public_json(Path(args.report_json), result)
            raise Phase36RunFailed("Controlled localization failed while saving/finalizing translations", result)

        result["after"] = after
        result["delta"] = metric_delta(before, after)
        result["remaining_missing"] = after["target_missing_visual_translations"]
        result["finished_at"] = utc_now()
        result["status"] = "completed" if result["failed"] == 0 else "failed_partial"
        result["success"] = result["failed"] == 0
        sys.stderr.write(
            f"completed_localization job_id={job.id} translated={result['translated']} "
            f"failed={result['failed']} skipped={result['skipped']}\n"
        )
        sys.stderr.flush()
        if args.report_json:
            write_public_json(Path(args.report_json), result)
        if result["failed"]:
            raise Phase36RunFailed("Controlled localization finished with failed candidates", result)
        return result
    finally:
        db.close()


def await_provider(provider: Any, tag_inputs: List[Dict[str, str]]) -> List[Any]:
    return asyncio.run(provider.translate_tags(tag_inputs))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-label", default=SOURCE_LABEL)
    parser.add_argument("--expected-media-count", type=int, default=995)
    parser.add_argument("--lang", default="zh-CN")

    sub = parser.add_subparsers(dest="command", required=True)

    baseline = sub.add_parser("baseline", help="Read-only baseline report")
    baseline.add_argument("--report-json")

    ai = sub.add_parser("ai-tag", help="Execute scoped Phase 3.6 AI tagging")
    ai.add_argument("--confirm-phase36", required=True)
    ai.add_argument("--db-backup-file", required=True)
    ai.add_argument("--report-json", required=True)
    ai.add_argument("--chunk-size", type=positive_int, default=10)
    ai.add_argument("--limit", type=positive_int, default=None)

    loc = sub.add_parser("localize", help="Execute scoped Phase 3.6 tag localization")
    loc.add_argument("--confirm-phase36", required=True)
    loc.add_argument("--db-backup-file", required=True)
    loc.add_argument("--report-json", required=True)
    loc.add_argument("--max-items", type=positive_int, default=500)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "baseline":
            payload = run_baseline(args)
        elif args.command == "ai-tag":
            payload = run_ai_tagging_controlled(args)
        elif args.command == "localize":
            payload = run_controlled_localization(args)
        else:
            parser.error(f"Unknown command: {args.command}")
            return 2
    except Phase36RunFailed as exc:
        sys.stderr.write(sanitize_public_text(str(exc)) + "\n")
        if exc.payload is not None:
            sys.stdout.write(json.dumps(sanitize_public_obj(exc.payload), ensure_ascii=False, indent=2) + "\n")
        return 1
    except Exception as exc:
        sys.stderr.write(sanitize_public_text(str(exc)) + "\n")
        return 1

    sys.stdout.write(json.dumps(sanitize_public_obj(payload), ensure_ascii=False, indent=2) + "\n")
    if payload.get("success") is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
