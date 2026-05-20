"""Controlled Phase 3.7 Tier-1000 classification and tag-scope audit.

This runner is intentionally narrower than the generic admin classification
API.  Write mode is locked to the Phase 3.5 import source label and creates
classification jobs only with explicit, non-empty media ID chunks.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import func, or_
from sqlalchemy.orm import Session


REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


SOURCE_LABEL = "violet:tier1000:phase3.5"
EXPECTED_MEDIA_COUNT = 995
CONFIRM_PHRASE = "PHASE37_TIER1000_CLASSIFICATION"
TRIGGER_SOURCE = "phase3.7"
AI_SOURCE = "ai_wd"
ELIGIBLE_CONTENT_CLASSES = ("anime", "unknown")
INELIGIBLE_CONTENT_CLASSES = ("illustration", "non_anime")
ACTIVE_JOB_STATUSES = ("pending", "running", "cancelling")
PUBLIC_PATH_REDACTION = "<redacted_path>"
SECRET_REDACTION = "<redacted_secret>"


class Phase37RunFailed(RuntimeError):
    """A controlled Phase 3.7 command wrote its report but must exit nonzero."""

    def __init__(self, message: str, payload: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.payload = payload


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def enum_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "value"):
        return str(value.value)
    text = str(value)
    return text.split(".", 1)[-1] if "." in text else text


def sanitize_public_text(value: str) -> str:
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


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


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


def redacted_db_url(settings: Any) -> str:
    return (
        f"postgresql://{settings.DB_USER}:***@"
        f"{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
    )


def target_media_count(db: Session, source_label: str) -> int:
    from app.models import Media

    return int(db.query(func.count(Media.id)).filter(Media.source == source_label).scalar() or 0)


def classification_distribution(db: Session, source_label: str) -> Dict[str, int]:
    from app.models import Media

    rows = (
        db.query(Media.content_class, func.count(Media.id))
        .filter(Media.source == source_label)
        .group_by(Media.content_class)
        .all()
    )
    result = {"anime": 0, "unknown": 0, "illustration": 0, "non_anime": 0, "unclassified": 0}
    for content_class, count in rows:
        key = enum_value(content_class) or "unclassified"
        result[key] = int(count)
    return result


def confidence_distribution(db: Session, source_label: str) -> Dict[str, int]:
    from app.models import Media

    rows = (
        db.query(Media.content_class_confidence)
        .filter(Media.source == source_label)
        .all()
    )
    result = {
        "null": 0,
        "lt_0_50": 0,
        "gte_0_50_lt_0_80": 0,
        "gte_0_80": 0,
    }
    for (confidence,) in rows:
        if confidence is None:
            result["null"] += 1
        elif confidence < 0.5:
            result["lt_0_50"] += 1
        elif confidence < 0.8:
            result["gte_0_50_lt_0_80"] += 1
        else:
            result["gte_0_80"] += 1
    return result


def sample_media_ids_by_class(db: Session, source_label: str, limit: int = 10) -> Dict[str, List[int]]:
    from app.enums import ContentClassEnum
    from app.models import Media

    samples: Dict[str, List[int]] = {}
    for name in ("anime", "unknown", "illustration", "non_anime"):
        cls = ContentClassEnum(name)
        samples[name] = [
            int(row[0])
            for row in (
                db.query(Media.id)
                .filter(Media.source == source_label, Media.content_class == cls)
                .order_by(Media.id.asc())
                .limit(limit)
                .all()
            )
        ]
    samples["unclassified"] = [
        int(row[0])
        for row in (
            db.query(Media.id)
            .filter(Media.source == source_label, Media.content_class.is_(None))
            .order_by(Media.id.asc())
            .limit(limit)
            .all()
        )
    ]
    return samples


def count_jobs(db: Session, model: Any) -> int:
    return int(db.query(func.count(model.id)).scalar() or 0)


def find_active_jobs(db: Session, model: Any) -> List[Dict[str, Any]]:
    rows = (
        db.query(model)
        .filter(model.status.in_(ACTIVE_JOB_STATUSES))
        .order_by(model.id.asc())
        .all()
    )
    return [
        {
            "id": int(job.id),
            "status": str(job.status),
            "trigger_source": str(getattr(job, "trigger_source", getattr(job, "source", ""))),
            "created_at": job.created_at.isoformat() if getattr(job, "created_at", None) else None,
            "started_at": job.started_at.isoformat() if getattr(job, "started_at", None) else None,
        }
        for job in rows
    ]


def select_target_media_ids(
    db: Session,
    source_label: str,
    *,
    only_unclassified: bool = True,
    limit: Optional[int] = None,
) -> List[int]:
    from app.models import Media

    query = db.query(Media.id).filter(Media.source == source_label)
    if only_unclassified:
        query = query.filter(Media.content_class.is_(None))
    query = query.order_by(Media.id.asc())
    if limit is not None:
        query = query.limit(limit)
    return [int(row[0]) for row in query.all()]


def count_target_ai_associations(db: Session, source_label: str) -> int:
    from app.models import Media, blombooru_media_tags

    return int(
        db.query(func.count())
        .select_from(blombooru_media_tags)
        .join(Media, Media.id == blombooru_media_tags.c.media_id)
        .filter(Media.source == source_label)
        .filter(blombooru_media_tags.c.source == AI_SOURCE)
        .scalar()
        or 0
    )


def count_target_translations(db: Session, source_label: str) -> int:
    from app.models import Media, Tag, TagTranslation, blombooru_media_tags

    return int(
        db.query(func.count(func.distinct(TagTranslation.canonical_name)))
        .select_from(TagTranslation)
        .join(Tag, Tag.name == TagTranslation.canonical_name)
        .join(blombooru_media_tags, blombooru_media_tags.c.tag_id == Tag.id)
        .join(Media, Media.id == blombooru_media_tags.c.media_id)
        .filter(Media.source == source_label)
        .filter(TagTranslation.language == "zh-CN")
        .filter(TagTranslation.status != "rejected")
        .scalar()
        or 0
    )


def collect_baseline(db: Session, source_label: str) -> Dict[str, Any]:
    from app.models import AITagJob, ClassificationJob, Media, Tag, TagTranslationJob

    target_count = target_media_count(db, source_label)
    distribution = classification_distribution(db, source_label)
    classified = target_count - distribution.get("unclassified", 0)
    return {
        "source_label": source_label,
        "target_media_count": target_count,
        "classified": classified,
        "unclassified": distribution.get("unclassified", 0),
        "content_class_distribution": distribution,
        "confidence_distribution": confidence_distribution(db, source_label),
        "sample_media_ids_by_class": sample_media_ids_by_class(db, source_label),
        "classification_jobs": count_jobs(db, ClassificationJob),
        "ai_jobs": count_jobs(db, AITagJob),
        "translation_jobs": count_jobs(db, TagTranslationJob),
        "tag_rows": int(db.query(func.count(Tag.id)).scalar() or 0),
        "target_ai_associations": count_target_ai_associations(db, source_label),
        "target_translated_tag_names": count_target_translations(db, source_label),
        "total_media": int(db.query(func.count(Media.id)).scalar() or 0),
    }


def validate_backup_file(path: Optional[Path]) -> Dict[str, Any]:
    if path is None:
        raise RuntimeError("--db-backup-file is required for classify")
    if not path.exists():
        raise RuntimeError("--db-backup-file does not exist")
    size = path.stat().st_size
    if size <= 0:
        raise RuntimeError("--db-backup-file must be non-empty")
    return {"basename": path.name, "size_bytes": int(size), "path_redacted": True}


def validate_common_write_gates(args: argparse.Namespace, db: Session, settings: Any) -> Dict[str, Any]:
    from app.models import AITagJob, ClassificationJob, TagTranslationJob
    from app.services.classification_job_service import is_classification_job_active

    if args.source_label != SOURCE_LABEL:
        raise RuntimeError(f"Phase 3.7 write modes are locked to source label {SOURCE_LABEL}")
    if args.confirm_phase37 != CONFIRM_PHRASE:
        raise RuntimeError(f"--confirm-phase37 must be exactly {CONFIRM_PHRASE}")
    if settings.VIOLET_ENV != "development":
        raise RuntimeError("Phase 3.7 write modes require VIOLET_ENV=development")
    if settings.DB_NAME != "blombooru":
        raise RuntimeError(f"Refusing write mode against unexpected DB {settings.DB_NAME!r}")
    if settings.IS_TEST_ENV:
        raise RuntimeError("Refusing Phase 3.7 real write mode with VIOLET_ENV=test")
    if not settings.CONTENT_CLASSIFICATION_ENABLED:
        raise RuntimeError("CONTENT_CLASSIFICATION_ENABLED must be true for Phase 3.7 classification")
    if settings.CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT:
        raise RuntimeError("CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT must be false for Phase 3.7")
    if getattr(settings, "AI_AUTO_TAG_AFTER_IMPORT", False):
        raise RuntimeError("AI_AUTO_TAG_AFTER_IMPORT must be false for Phase 3.7")
    if getattr(settings, "AI_TAGGING_AUTO_LOCALIZATION", False):
        raise RuntimeError("AI_TAGGING_AUTO_LOCALIZATION must be false for Phase 3.7")
    if settings.TAG_TRANSLATION_BG_ENABLED or settings.TAG_TRANSLATION_AUTO_ENABLED:
        raise RuntimeError("Tag translation background/auto workers must be disabled for Phase 3.7")
    if settings.TAG_TRANSLATION_LLM_ENABLED:
        raise RuntimeError("TAG_TRANSLATION_LLM_ENABLED must be false for Phase 3.7")
    if settings.ENTITY_ALIAS_RESOLVER_ENABLED:
        raise RuntimeError("Entity Alias Resolver must be disabled for Phase 3.7")
    if int(settings.CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS) <= 0:
        raise RuntimeError("CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS must be a positive integer")

    actual_count = target_media_count(db, args.source_label)
    if actual_count != args.expected_media_count:
        raise RuntimeError(
            f"Target media count mismatch: expected {args.expected_media_count}, got {actual_count}"
        )

    active_classification = find_active_jobs(db, ClassificationJob)
    active_ai = find_active_jobs(db, AITagJob)
    active_translation = find_active_jobs(db, TagTranslationJob)
    if active_classification or is_classification_job_active():
        raise RuntimeError(f"Active classification jobs exist: {active_classification}")
    if active_ai:
        raise RuntimeError(f"Active AI tagging jobs exist: {active_ai}")
    if active_translation:
        raise RuntimeError(f"Active tag translation jobs exist: {active_translation}")

    backup = validate_backup_file(Path(args.db_backup_file) if args.db_backup_file else None)
    return {
        "violet_env": settings.VIOLET_ENV,
        "db_name": settings.DB_NAME,
        "db_url_redacted": redacted_db_url(settings),
        "source_label_locked": True,
        "expected_media_count": args.expected_media_count,
        "content_classification_enabled": True,
        "content_classification_method": settings.CONTENT_CLASSIFICATION_METHOD,
        "content_classification_batch_max_items": int(settings.CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS),
        "content_classification_auto_after_import_disabled": True,
        "ai_auto_tag_after_import_disabled": True,
        "tag_translation_disabled": True,
        "entity_alias_resolver_disabled": True,
        "backup": backup,
    }


def serialize_classification_job(job: Any) -> Dict[str, Any]:
    failed_items: List[Dict[str, Any]] = []
    if getattr(job, "failed_items_json", None):
        try:
            failed_items = json.loads(job.failed_items_json)
        except Exception:
            failed_items = [{"error": "failed_items_json_parse_error"}]
    return {
        "id": int(job.id),
        "status": str(job.status),
        "media_ids_count": len(json.loads(job.media_ids_json)) if job.media_ids_json else 0,
        "max_items": int(job.max_items or 0),
        "processed": int(job.processed or 0),
        "classified_anime": int(job.classified_anime or 0),
        "classified_non_anime": int(job.classified_non_anime or 0),
        "classified_unknown": int(job.classified_unknown or 0),
        "failed": int(job.failed or 0),
        "failed_items": sanitize_public_obj(failed_items[:10]),
        "error_message": sanitize_public_text(job.error_message) if job.error_message else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def chunked(items: Sequence[int], size: int) -> Iterable[List[int]]:
    for start in range(0, len(items), size):
        yield list(items[start:start + size])


def apply_failure_status(payload: Dict[str, Any], *, status: str, error: str) -> Dict[str, Any]:
    payload["success"] = False
    payload["status"] = status
    payload.setdefault("errors", []).append(sanitize_public_text(error))
    payload["finished_at"] = utc_now()
    return payload


def run_classification_controlled(args: argparse.Namespace) -> Dict[str, Any]:
    ctx = load_app_context()
    settings = ctx["settings"]
    db = ctx["database"].SessionLocal()
    report_path = Path(args.report_json) if args.report_json else None

    try:
        before = collect_baseline(db, args.source_label)
        gates = validate_common_write_gates(args, db, settings)
        media_ids = select_target_media_ids(
            db,
            args.source_label,
            only_unclassified=not args.force_reclassify,
            limit=args.limit,
        )
        configured_batch_max = int(settings.CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS)
        effective_chunk_size = min(args.chunk_size, configured_batch_max)
        if effective_chunk_size <= 0:
            raise RuntimeError("--chunk-size and CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS must be positive")

        payload: Dict[str, Any] = {
            "phase": "3.7",
            "mode": "classify",
            "source_label": args.source_label,
            "started_at": utc_now(),
            "success": False,
            "status": "running",
            "dry_run": False,
            "requested_limit": args.limit,
            "requested_chunk_size": args.chunk_size,
            "effective_chunk_size": effective_chunk_size,
            "configured_batch_max": configured_batch_max,
            "write_gates": gates,
            "before": before,
            "target_media_ids_count": len(media_ids),
            "jobs": [],
            "totals": {
                "processed": 0,
                "classified_anime": 0,
                "classified_non_anime": 0,
                "classified_unknown": 0,
                "failed": 0,
            },
            "errors": [],
        }

        if not media_ids:
            after = collect_baseline(db, args.source_label)
            payload["after"] = after
            payload["success"] = after["unclassified"] == 0
            payload["status"] = "noop_already_classified" if payload["success"] else "failed_no_target_ids"
            payload["finished_at"] = utc_now()
            if report_path:
                write_public_json(report_path, payload)
            if not payload["success"]:
                raise Phase37RunFailed("No explicit media IDs selected while target set remains unclassified", payload)
            return payload

        from app.models import ClassificationJob
        from app.services.classification_job_service import (
            create_classification_job,
            run_classification_job,
        )

        for chunk_index, chunk in enumerate(chunked(media_ids, effective_chunk_size), start=1):
            if not chunk:
                raise RuntimeError("Internal error: empty classification chunk")
            job = create_classification_job(
                db,
                media_ids=chunk,
                max_items=len(chunk),
                only_unclassified=not args.force_reclassify,
                force_reclassify=args.force_reclassify,
                trigger_source=TRIGGER_SOURCE,
            )
            if not job.media_ids_json:
                raise RuntimeError("Classification job was created without explicit media_ids_json")
            run_classification_job(job.id)
            db.expire_all()
            job = db.query(ClassificationJob).get(job.id)
            job_payload = serialize_classification_job(job)
            job_payload["chunk_index"] = chunk_index
            job_payload["requested_media_count"] = len(chunk)
            payload["jobs"].append(job_payload)
            for key in payload["totals"]:
                payload["totals"][key] += int(job_payload.get(key, 0) or 0)

            if job.status != "completed" or int(job.failed or 0) > 0:
                after = collect_baseline(db, args.source_label)
                payload["after"] = after
                payload["safety"] = side_effect_deltas(before, after)
                apply_failure_status(
                    payload,
                    status="failed_classification_job",
                    error=f"Classification job {job.id} ended with status={job.status}, failed={job.failed}",
                )
                if report_path:
                    write_public_json(report_path, payload)
                raise Phase37RunFailed("Classification job failed", payload)

        after = collect_baseline(db, args.source_label)
        payload["after"] = after
        payload["safety"] = side_effect_deltas(before, after)
        payload["finished_at"] = utc_now()

        if after["unclassified"] != 0:
            apply_failure_status(
                payload,
                status="failed_unclassified_remaining",
                error=f"{after['unclassified']} target media remain unclassified after Phase 3.7 classification",
            )
            if report_path:
                write_public_json(report_path, payload)
            raise Phase37RunFailed("Target media remain unclassified", payload)

        if payload["safety"].get("ai_jobs_delta") != 0 or payload["safety"].get("translation_jobs_delta") != 0:
            apply_failure_status(
                payload,
                status="failed_phase_isolation_violation",
                error="AI or translation jobs changed during classification",
            )
            if report_path:
                write_public_json(report_path, payload)
            raise Phase37RunFailed("Forbidden Phase 3.7 side-effect jobs changed", payload)

        payload["success"] = True
        payload["status"] = "completed"
        if report_path:
            write_public_json(report_path, payload)
        return payload
    except Phase37RunFailed:
        raise
    except Exception as exc:
        payload = {
            "phase": "3.7",
            "mode": "classify",
            "source_label": getattr(args, "source_label", SOURCE_LABEL),
            "success": False,
            "status": "failed_exception",
            "errors": [sanitize_public_text(str(exc))],
            "finished_at": utc_now(),
        }
        if report_path:
            write_public_json(report_path, payload)
        raise
    finally:
        db.close()


def side_effect_deltas(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, int]:
    return {
        "classification_jobs_delta": int(after["classification_jobs"] - before["classification_jobs"]),
        "ai_jobs_delta": int(after["ai_jobs"] - before["ai_jobs"]),
        "translation_jobs_delta": int(after["translation_jobs"] - before["translation_jobs"]),
        "tag_rows_delta": int(after["tag_rows"] - before["tag_rows"]),
        "target_ai_associations_delta": int(after["target_ai_associations"] - before["target_ai_associations"]),
        "target_translated_tag_names_delta": int(
            after["target_translated_tag_names"] - before["target_translated_tag_names"]
        ),
    }


def content_class_conditions_for_names(names: Sequence[str]):
    from app.enums import ContentClassEnum
    from app.models import Media

    conditions = []
    for name in names:
        conditions.append(Media.content_class == ContentClassEnum(name))
    return conditions


def count_media_by_conditions(db: Session, source_label: str, conditions: Sequence[Any]) -> int:
    from app.models import Media

    if not conditions:
        return 0
    return int(
        db.query(func.count(Media.id))
        .filter(Media.source == source_label)
        .filter(or_(*conditions))
        .scalar()
        or 0
    )


def audit_tag_scope(db: Session, source_label: str, lang: str = "zh-CN") -> Dict[str, Any]:
    from app.enums import ContentClassEnum
    from app.models import Media, Tag, TagTranslation, blombooru_media_tags

    eligible_conditions = content_class_conditions_for_names(ELIGIBLE_CONTENT_CLASSES)
    ineligible_conditions = content_class_conditions_for_names(INELIGIBLE_CONTENT_CLASSES) + [
        Media.content_class.is_(None)
    ]

    eligible_media_count = count_media_by_conditions(db, source_label, eligible_conditions)
    ineligible_media_count = count_media_by_conditions(db, source_label, ineligible_conditions)

    base_assoc = (
        db.query(blombooru_media_tags.c.media_id, blombooru_media_tags.c.tag_id)
        .join(Media, Media.id == blombooru_media_tags.c.media_id)
        .filter(Media.source == source_label)
        .filter(blombooru_media_tags.c.source == AI_SOURCE)
    )

    ineligible_assoc_query = base_assoc.filter(or_(*ineligible_conditions))
    eligible_assoc_query = base_assoc.filter(or_(*eligible_conditions))

    ineligible_ai_associations = int(ineligible_assoc_query.count() or 0)
    ineligible_media_with_ai_tags = int(
        db.query(func.count(func.distinct(blombooru_media_tags.c.media_id)))
        .select_from(blombooru_media_tags)
        .join(Media, Media.id == blombooru_media_tags.c.media_id)
        .filter(Media.source == source_label)
        .filter(blombooru_media_tags.c.source == AI_SOURCE)
        .filter(or_(*ineligible_conditions))
        .scalar()
        or 0
    )
    eligible_ai_associations = int(eligible_assoc_query.count() or 0)

    ineligible_samples = [
        int(row[0])
        for row in (
            db.query(Media.id)
            .filter(Media.source == source_label)
            .filter(or_(*ineligible_conditions))
            .order_by(Media.id.asc())
            .limit(20)
            .all()
        )
    ]

    translated_tags_on_ineligible = int(
        db.query(func.count(func.distinct(TagTranslation.canonical_name)))
        .select_from(TagTranslation)
        .join(Tag, Tag.name == TagTranslation.canonical_name)
        .join(blombooru_media_tags, blombooru_media_tags.c.tag_id == Tag.id)
        .join(Media, Media.id == blombooru_media_tags.c.media_id)
        .filter(Media.source == source_label)
        .filter(or_(*ineligible_conditions))
        .filter(TagTranslation.language == lang)
        .filter(TagTranslation.status != "rejected")
        .scalar()
        or 0
    )

    translated_tags_on_eligible = int(
        db.query(func.count(func.distinct(TagTranslation.canonical_name)))
        .select_from(TagTranslation)
        .join(Tag, Tag.name == TagTranslation.canonical_name)
        .join(blombooru_media_tags, blombooru_media_tags.c.tag_id == Tag.id)
        .join(Media, Media.id == blombooru_media_tags.c.media_id)
        .filter(Media.source == source_label)
        .filter(or_(*eligible_conditions))
        .filter(TagTranslation.language == lang)
        .filter(TagTranslation.status != "rejected")
        .scalar()
        or 0
    )

    distinct_tags_on_eligible = int(
        db.query(func.count(func.distinct(blombooru_media_tags.c.tag_id)))
        .select_from(blombooru_media_tags)
        .join(Media, Media.id == blombooru_media_tags.c.media_id)
        .filter(Media.source == source_label)
        .filter(or_(*eligible_conditions))
        .filter(blombooru_media_tags.c.source == AI_SOURCE)
        .scalar()
        or 0
    )
    distinct_tags_on_ineligible = int(
        db.query(func.count(func.distinct(blombooru_media_tags.c.tag_id)))
        .select_from(blombooru_media_tags)
        .join(Media, Media.id == blombooru_media_tags.c.media_id)
        .filter(Media.source == source_label)
        .filter(or_(*ineligible_conditions))
        .filter(blombooru_media_tags.c.source == AI_SOURCE)
        .scalar()
        or 0
    )

    class_breakdown = classification_distribution(db, source_label)
    return {
        "eligible_classes": list(ELIGIBLE_CONTENT_CLASSES),
        "ineligible_classes": list(INELIGIBLE_CONTENT_CLASSES) + ["unclassified"],
        "eligible_media_count": eligible_media_count,
        "ineligible_media_count": ineligible_media_count,
        "content_class_distribution": class_breakdown,
        "ai_tags": {
            "eligible_associations": eligible_ai_associations,
            "ineligible_associations": ineligible_ai_associations,
            "ineligible_media_with_ai_tags": ineligible_media_with_ai_tags,
            "ineligible_media_id_sample": ineligible_samples,
            "distinct_ai_tags_on_eligible_media": distinct_tags_on_eligible,
            "distinct_ai_tags_on_ineligible_media": distinct_tags_on_ineligible,
            "mutation_performed": False,
        },
        "localization": {
            "tag_translations_are_tag_level_shared_records": True,
            "translated_tag_names_attached_to_eligible_media": translated_tags_on_eligible,
            "translated_tag_names_attached_to_ineligible_media": translated_tags_on_ineligible,
            "mutation_performed": False,
            "policy": (
                "Future localization candidate selection must count only tags attached "
                "to media whose content_class is anime or unknown."
            ),
        },
        "future_gate": {
            "classify_before_ai_tagging": True,
            "ai_tagging_allowed_classes": list(ELIGIBLE_CONTENT_CLASSES),
            "localization_allowed_source_media_classes": list(ELIGIBLE_CONTENT_CLASSES),
            "tag_stats_allowed_source_media_classes": list(ELIGIBLE_CONTENT_CLASSES),
            "tag_similarity_allowed_source_media_classes": list(ELIGIBLE_CONTENT_CLASSES),
            "excluded_classes": list(INELIGIBLE_CONTENT_CLASSES) + ["unclassified"],
        },
    }


def run_scope_audit(args: argparse.Namespace) -> Dict[str, Any]:
    db = new_session()
    report_path = Path(args.report_json) if args.report_json else None
    try:
        baseline = collect_baseline(db, args.source_label)
        if args.expected_media_count is not None and baseline["target_media_count"] != args.expected_media_count:
            raise RuntimeError(
                f"Target media count mismatch: expected {args.expected_media_count}, "
                f"got {baseline['target_media_count']}"
            )
        payload = {
            "phase": "3.7",
            "mode": "scope-audit",
            "source_label": args.source_label,
            "success": True,
            "status": "completed",
            "created_at": utc_now(),
            "baseline": baseline,
            "scope_audit": audit_tag_scope(db, args.source_label, args.lang),
            "write_performed": False,
        }
        if report_path:
            write_public_json(report_path, payload)
        return payload
    except Exception as exc:
        payload = {
            "phase": "3.7",
            "mode": "scope-audit",
            "source_label": getattr(args, "source_label", SOURCE_LABEL),
            "success": False,
            "status": "failed_exception",
            "errors": [sanitize_public_text(str(exc))],
            "finished_at": utc_now(),
            "write_performed": False,
        }
        if report_path:
            write_public_json(report_path, payload)
        raise
    finally:
        db.close()


def run_baseline(args: argparse.Namespace) -> Dict[str, Any]:
    db = new_session()
    report_path = Path(args.report_json) if args.report_json else None
    try:
        payload = {
            "phase": "3.7",
            "mode": "baseline",
            "source_label": args.source_label,
            "success": True,
            "status": "completed",
            "created_at": utc_now(),
            "baseline": collect_baseline(db, args.source_label),
            "write_performed": False,
        }
        if report_path:
            write_public_json(report_path, payload)
        return payload
    finally:
        db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 3.7 Tier-1000 classification scope gate")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--source-label", default=SOURCE_LABEL)
        p.add_argument("--expected-media-count", type=positive_int, default=EXPECTED_MEDIA_COUNT)
        p.add_argument("--report-json")

    baseline = sub.add_parser("baseline", help="Read-only baseline report")
    add_common(baseline)

    classify = sub.add_parser("classify", help="Execute scoped Phase 3.7 classification")
    add_common(classify)
    classify.add_argument("--confirm-phase37", required=True)
    classify.add_argument("--db-backup-file", required=True)
    classify.add_argument("--chunk-size", type=positive_int, default=100)
    classify.add_argument("--limit", type=positive_int, default=None)
    classify.add_argument("--force-reclassify", action="store_true")

    scope = sub.add_parser("scope-audit", help="Read-only tag scope audit")
    add_common(scope)
    scope.add_argument("--lang", default="zh-CN")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "baseline":
            payload = run_baseline(args)
        elif args.command == "classify":
            payload = run_classification_controlled(args)
        elif args.command == "scope-audit":
            payload = run_scope_audit(args)
        else:
            parser.error(f"Unsupported command {args.command!r}")
            return 2
        if not payload.get("success", False):
            return 1
        return 0
    except Phase37RunFailed as exc:
        print(sanitize_public_text(str(exc)), file=sys.stderr)
        return 1
    except Exception as exc:
        print(sanitize_public_text(str(exc)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
