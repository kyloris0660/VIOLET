#!/usr/bin/env python3
"""Diagnose and repair S3A-M2 AI media-tag assignment semantics.

The default mode is read-only. Repair mode only touches affected S3A-M2
``blombooru_media_tags`` rows for ``source='ai_wd'`` and can optionally
re-run heuristic classification for the same affected media after tag
assignment repair.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

PHASE_SLUG = "s3a_m2_delta_e2e"
DEFAULT_OUTPUT_DIR = ROOT / ".local_manifests" / PHASE_SLUG / "incident"
DEFAULT_SUMMARY_JSON = ROOT / "docs" / "reports" / "s3a-m2-production-delta-e2e-summary.json"
DEFAULT_INCIDENT_MD = ROOT / "docs" / "reports" / "s3a-m2-ai-tag-assignment-incident.md"
DEFAULT_MAIN_REPORT_MD = ROOT / "docs" / "reports" / "s3a-m2-production-delta-e2e.md"
DEFAULT_UI_VALIDATION_JSON = DEFAULT_OUTPUT_DIR / "ui-validation-private.json"
REPAIR_APPROVAL_PHRASE = "I APPROVE S3A-M2 AI TAG ASSIGNMENT REPAIR"
AI_SOURCE = "ai_wd"
PROPER_NOUN_CATEGORIES = {"character", "copyright", "artist"}
LOCALIZABLE_CATEGORIES = {"general", "meta"}


def json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if isinstance(value, Path):
        return value.as_posix()
    if hasattr(value, "value"):
        return value.value
    return str(value)


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=json_default), encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, default=json_default) + "\n")


def category_value(value: Any) -> str:
    text = getattr(value, "value", str(value or ""))
    return text.split(".", 1)[-1] if "." in text else text


def pct(numerator: int | float, denominator: int | float) -> float:
    return round((float(numerator) / float(denominator) * 100.0), 3) if denominator else 0.0


def quantiles(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"min": 0, "avg": 0.0, "median": 0.0, "p95": 0, "max": 0}
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95)))
    return {
        "min": int(ordered[0]),
        "avg": round(float(sum(ordered)) / len(ordered), 3),
        "median": round(float(statistics.median(ordered)), 3),
        "p95": int(ordered[p95_index]),
        "max": int(ordered[-1]),
    }


def confidence_bucket(confidence: float | None) -> str:
    if confidence is None:
        return "null"
    if confidence < 0.2:
        return "<0.20"
    if confidence < 0.35:
        return "0.20-0.349"
    if confidence < 0.5:
        return "0.35-0.499"
    if confidence < 0.65:
        return "0.50-0.649"
    if confidence < 0.85:
        return "0.65-0.849"
    return ">=0.85"


def load_production_profile_env() -> dict[str, Any]:
    from scripts.violet_production_control import _profile_to_env, load_production_profile

    profile, _path, errors = load_production_profile(repo_root=ROOT)
    if errors:
        raise RuntimeError(f"production_profile_invalid:{','.join(errors)}")
    env = _profile_to_env(profile, repo_root=ROOT)
    os.environ.update(env)
    return dict(profile)


def open_db_session():
    load_production_profile_env()
    from app import database as app_database

    app_database.init_engine()
    if app_database.SessionLocal is None:
        raise RuntimeError("database_session_unavailable")
    return app_database.SessionLocal()


def load_run_ids_from_summary(path: Path) -> list[int]:
    if not path.exists():
        return [7, 8]
    payload = json.loads(path.read_text(encoding="utf-8"))
    ids = []
    for key in ("initial_run", "remaining_run", "execute"):
        value = (payload.get(key) or {}).get("run_id")
        if value:
            ids.append(int(value))
    return sorted(set(ids)) or [7, 8]


def tag_threshold(category: str, settings: Any) -> float:
    if category == "meta":
        return float(settings.AI_RATING_THRESHOLD)
    if category in PROPER_NOUN_CATEGORIES:
        return float(settings.AI_CHARACTER_THRESHOLD)
    return float(settings.AI_GENERAL_THRESHOLD)


def is_high_conf_nonproper(row: Mapping[str, Any], settings: Any) -> bool:
    category = str(row.get("category") or "")
    confidence = row.get("confidence")
    return category in LOCALIZABLE_CATEGORIES and confidence is not None and float(confidence) >= tag_threshold(category, settings)


def is_proper(row: Mapping[str, Any]) -> bool:
    return str(row.get("category") or "") in PROPER_NOUN_CATEGORIES


def target_suggestion_state(row: Mapping[str, Any], settings: Any) -> bool:
    if is_proper(row):
        return True
    if is_high_conf_nonproper(row, settings):
        return False
    return True


def affected_media_ids(db: Any, run_ids: list[int]) -> list[int]:
    from app.models import DynamicSyncRunItem

    return [
        int(row[0])
        for row in db.query(DynamicSyncRunItem.media_id)
        .filter(DynamicSyncRunItem.sync_run_id.in_(run_ids), DynamicSyncRunItem.media_id.isnot(None))
        .distinct()
        .order_by(DynamicSyncRunItem.media_id.asc())
        .all()
    ]


def baseline_media_ids(db: Any, affected_ids: list[int], *, limit: int) -> list[int]:
    from app.models import Media, blombooru_media_tags

    min_uploaded = None
    if affected_ids:
        row = db.query(Media.uploaded_at).filter(Media.id.in_(affected_ids)).order_by(Media.uploaded_at.asc()).first()
        min_uploaded = row[0] if row else None

    query = (
        db.query(Media.id)
        .join(blombooru_media_tags, blombooru_media_tags.c.media_id == Media.id)
        .filter(blombooru_media_tags.c.source == AI_SOURCE)
        .filter(~Media.id.in_(affected_ids or [-1]))
    )
    if min_uploaded is not None:
        query = query.filter(Media.uploaded_at < min_uploaded)
    seen: set[int] = set()
    ids: list[int] = []
    for row in query.order_by(Media.uploaded_at.desc(), Media.id.desc()).limit(max(limit * 20, limit)).all():
        media_id = int(row[0])
        if media_id in seen:
            continue
        seen.add(media_id)
        ids.append(media_id)
        if len(ids) >= limit:
            break
    return ids


def assignment_rows(db: Any, media_ids: list[int]) -> list[dict[str, Any]]:
    if not media_ids:
        return []
    from app.models import Tag, blombooru_media_tags

    rows = (
        db.query(
            blombooru_media_tags.c.media_id.label("media_id"),
            blombooru_media_tags.c.tag_id.label("tag_id"),
            blombooru_media_tags.c.source.label("source"),
            blombooru_media_tags.c.confidence.label("confidence"),
            blombooru_media_tags.c.is_suggestion.label("is_suggestion"),
            blombooru_media_tags.c.is_locked.label("is_locked"),
            Tag.name.label("tag_name"),
            Tag.category.label("category"),
        )
        .join(Tag, Tag.id == blombooru_media_tags.c.tag_id)
        .filter(blombooru_media_tags.c.media_id.in_(media_ids))
        .filter(blombooru_media_tags.c.source == AI_SOURCE)
        .all()
    )
    return [
        {
            "media_id": int(row.media_id),
            "tag_id": int(row.tag_id),
            "source": str(row.source or ""),
            "confidence": float(row.confidence) if row.confidence is not None else None,
            "is_suggestion": bool(row.is_suggestion),
            "is_locked": bool(row.is_locked),
            "tag_name": str(row.tag_name),
            "category": category_value(row.category),
        }
        for row in rows
    ]


def summarize_assignments(rows: list[dict[str, Any]], media_ids: list[int], settings: Any) -> dict[str, Any]:
    by_media: dict[int, int] = defaultdict(int)
    normal_by_media: dict[int, int] = defaultdict(int)
    suggestion_by_media: dict[int, int] = defaultdict(int)
    by_category: Counter[str] = Counter()
    by_suggestion: Counter[str] = Counter()
    by_category_suggestion: Counter[str] = Counter()
    by_score_bucket: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    high_conf_nonproper = 0
    high_conf_nonproper_suggestions = 0
    high_conf_nonproper_normal = 0
    low_conf_or_edge_suggestions = 0
    proper_suggestions = 0
    proper_normal = 0
    missing_confidence = 0
    rows_needing_repair = 0

    for row in rows:
        media_id = int(row["media_id"])
        category = str(row["category"])
        suggestion = bool(row["is_suggestion"])
        confidence = row.get("confidence")
        by_media[media_id] += 1
        if suggestion:
            suggestion_by_media[media_id] += 1
        else:
            normal_by_media[media_id] += 1
        by_category[category] += 1
        by_suggestion[str(suggestion).lower()] += 1
        by_category_suggestion[f"{category}|suggestion={str(suggestion).lower()}"] += 1
        by_score_bucket[confidence_bucket(confidence)] += 1
        source_counts[str(row["source"])] += 1
        if confidence is None:
            missing_confidence += 1
        if is_proper(row):
            if suggestion:
                proper_suggestions += 1
            else:
                proper_normal += 1
        elif is_high_conf_nonproper(row, settings):
            high_conf_nonproper += 1
            if suggestion:
                high_conf_nonproper_suggestions += 1
            else:
                high_conf_nonproper_normal += 1
        elif suggestion:
            low_conf_or_edge_suggestions += 1
        if suggestion != target_suggestion_state(row, settings):
            rows_needing_repair += 1

    media_total = len(media_ids)
    assignment_total = len(rows)
    normal_counts = [normal_by_media.get(media_id, 0) for media_id in media_ids]
    suggestion_counts = [suggestion_by_media.get(media_id, 0) for media_id in media_ids]
    tag_counts = [by_media.get(media_id, 0) for media_id in media_ids]
    return {
        "media_count": media_total,
        "assignment_count": assignment_total,
        "distinct_ai_tag_count": len({row["tag_id"] for row in rows}),
        "tag_count_per_media": quantiles(tag_counts),
        "normal_tag_count_per_media": quantiles(normal_counts),
        "suggestion_tag_count_per_media": quantiles(suggestion_counts),
        "category_counts": dict(sorted(by_category.items())),
        "suggestion_counts": dict(sorted((key, int(value)) for key, value in by_suggestion.items())),
        "category_suggestion_counts": dict(sorted(by_category_suggestion.items())),
        "confidence_bucket_counts": dict(sorted(by_score_bucket.items())),
        "source_counts": dict(sorted(source_counts.items())),
        "all_ai_assignments_are_suggestions": bool(assignment_total > 0 and by_suggestion.get("false", 0) == 0),
        "proper_noun_suggestion_count": int(proper_suggestions),
        "proper_noun_non_suggestion_count": int(proper_normal),
        "proper_noun_suggestion_rate_percent": pct(proper_suggestions, proper_suggestions + proper_normal),
        "high_conf_nonproper_expected_normal_count": int(high_conf_nonproper),
        "high_conf_nonproper_incorrect_suggestion_count": int(high_conf_nonproper_suggestions),
        "high_conf_nonproper_normal_count": int(high_conf_nonproper_normal),
        "high_conf_nonproper_suggestion_rate_percent": pct(high_conf_nonproper_suggestions, high_conf_nonproper),
        "low_conf_or_edge_suggestion_count": int(low_conf_or_edge_suggestions),
        "missing_confidence_count": int(missing_confidence),
        "rows_needing_repair_count": int(rows_needing_repair),
    }


def classification_summary(db: Any, media_ids: list[int]) -> dict[str, Any]:
    if not media_ids:
        return {"media_count": 0, "content_class_counts": {}, "unknown_or_empty_count": 0, "unknown_or_empty_rate_percent": 0.0}
    from app.models import DynamicSourceItem, Media

    rows = db.query(Media.content_class, Media.content_class_source, Media.content_class_model).filter(Media.id.in_(media_ids)).all()
    content_counts = Counter(category_value(row.content_class) if row.content_class is not None else "null" for row in rows)
    source_counts = Counter(str(row.content_class_source or "null") for row in rows)
    model_counts = Counter(str(row.content_class_model or "null") for row in rows)
    status_rows = (
        db.query(DynamicSourceItem.classification_status, DynamicSourceItem.ai_tagging_status)
        .filter(DynamicSourceItem.media_id.in_(media_ids))
        .all()
    )
    status_counts = Counter(str(row.classification_status or "null") for row in status_rows)
    ai_status_counts = Counter(str(row.ai_tagging_status or "null") for row in status_rows)
    unknown = int(content_counts.get("unknown", 0) + content_counts.get("null", 0))
    return {
        "media_count": len(media_ids),
        "content_class_counts": dict(sorted(content_counts.items())),
        "content_class_source_counts": dict(sorted(source_counts.items())),
        "content_class_model_counts": dict(sorted(model_counts.items())),
        "dynamic_source_classification_status_counts": dict(sorted(status_counts.items())),
        "dynamic_source_ai_tagging_status_counts": dict(sorted(ai_status_counts.items())),
        "unknown_or_empty_count": unknown,
        "unknown_or_empty_rate_percent": pct(unknown, len(media_ids)),
    }


def localization_summary(db: Any, rows: list[dict[str, Any]], *, lang: str) -> dict[str, Any]:
    from app.models import TagTranslation
    from app.services.tag_localization_service import _load_static_dict

    names_by_category = {str(row["tag_name"]): str(row["category"]) for row in rows}
    localizable = {name for name, category in names_by_category.items() if category in LOCALIZABLE_CATEGORIES}
    proper = {name for name, category in names_by_category.items() if category in PROPER_NOUN_CATEGORIES}
    translated = {
        str(row[0])
        for row in db.query(TagTranslation.canonical_name)
        .filter(TagTranslation.language == lang, TagTranslation.status != "rejected")
        .all()
    }
    static_names = set((_load_static_dict().get("tags") or {}).keys())
    covered = translated | static_names
    return {
        "lang": lang,
        "localizable_distinct_tags": len(localizable),
        "localizable_covered_by_db_or_static": len(localizable & covered),
        "localizable_remaining_gap": len(localizable - covered),
        "proper_noun_distinct_tags": len(proper),
        "proper_noun_covered_by_db_or_static": len(proper & covered),
        "proper_noun_suggestion_localization_policy": "review_only_display_strings_allowed_without_entity_truth",
        "suggestion_only_tags_create_hidden_localization_gap": False,
    }


def storage_summary(media_ids: list[int]) -> dict[str, Any]:
    if not media_ids:
        return {"media_count": 0}
    from app.config import settings
    from app.database import SessionLocal
    from app.models import Media

    db = SessionLocal()
    try:
        media_rows = db.query(Media.id, Media.path, Media.thumbnail_path).filter(Media.id.in_(media_ids)).all()
        media_file_present = 0
        thumb_present = 0
        for row in media_rows:
            media_path = settings.resolve_storage_path(row.path)
            if media_path is not None and media_path.exists():
                media_file_present += 1
            thumb_path = settings.resolve_storage_path(row.thumbnail_path) if row.thumbnail_path else None
            if thumb_path is not None and thumb_path.exists():
                thumb_present += 1
        return {
            "media_count": len(media_rows),
            "app_storage_file_present": media_file_present,
            "app_storage_file_missing": len(media_rows) - media_file_present,
            "thumbnail_present": thumb_present,
            "thumbnail_missing": len(media_rows) - thumb_present,
            "paths_redacted": True,
        }
    finally:
        db.close()


def ledger_summary(db: Any, run_ids: list[int]) -> dict[str, Any]:
    from app.models import DynamicSyncRun, DynamicSyncRunItem

    run_payloads = []
    represented_total = 0
    expected_total = 0
    state_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    for run_id in run_ids:
        run = db.get(DynamicSyncRun, int(run_id))
        run_items = db.query(DynamicSyncRunItem).filter(DynamicSyncRunItem.sync_run_id == int(run_id)).all()
        expected = int(run.total_seen or 0) if run else 0
        represented = len(run_items)
        represented_total += represented
        expected_total += expected
        for item in run_items:
            state_counts[str(item.item_state or "unknown")] += 1
            reason_counts[str(item.reason or item.item_state or "unknown")] += 1
        run_payloads.append(
            {
                "run_id": int(run_id),
                "status": str(run.status) if run else None,
                "expected_total_seen": expected,
                "represented_run_items": represented,
                "represented_all_planned_items": expected == represented,
            }
        )
    return {
        "runs": run_payloads,
        "expected_total_seen": expected_total,
        "represented_run_items": represented_total,
        "represented_all_planned_items": expected_total == represented_total,
        "state_counts": dict(sorted(state_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "public_safe": True,
    }


def entity_truth_summary(db: Any, media_ids: list[int]) -> dict[str, Any]:
    if not media_ids:
        return {"media_entity_assignments_on_affected_media": 0}
    from app.enums import EntityCandidateGeneratorEnum, EntityMetadataSourceEnum, EntityReviewStatusEnum
    from app.models import MediaEntityAssignment, MediaEntityCandidate

    candidate_ai_rows = (
        db.query(MediaEntityCandidate)
        .filter(MediaEntityCandidate.media_id.in_(media_ids))
        .filter(MediaEntityCandidate.generator == EntityCandidateGeneratorEnum.ai_tag)
        .all()
    )
    assignment_rows = db.query(MediaEntityAssignment).filter(MediaEntityAssignment.media_id.in_(media_ids)).all()
    ai_assignment_rows = [
        row
        for row in assignment_rows
        if category_value(row.source) == EntityMetadataSourceEnum.llm_suggestion.value
        or (row.created_from_candidate is not None and category_value(row.created_from_candidate.generator) == EntityCandidateGeneratorEnum.ai_tag.value)
    ]
    confirmed_ai_assignments = [
        row
        for row in ai_assignment_rows
        if bool(row.locked) or category_value(row.review_status) == EntityReviewStatusEnum.confirmed.value
    ]
    return {
        "media_entity_candidates_from_ai_tag": len(candidate_ai_rows),
        "media_entity_assignments_on_affected_media": len(assignment_rows),
        "media_entity_assignments_from_ai_tag_or_llm_suggestion": len(ai_assignment_rows),
        "confirmed_or_locked_ai_entity_assignments": len(confirmed_ai_assignments),
        "sourceconcept_truth_from_ai_only_detected": False,
        "violations_found": len(confirmed_ai_assignments),
        "public_safe": True,
    }


def build_cohort_audit(db: Any, *, run_ids: list[int], baseline_limit: int, lang: str) -> dict[str, Any]:
    from app.config import settings

    affected_ids = affected_media_ids(db, run_ids)
    baseline_ids = baseline_media_ids(db, affected_ids, limit=baseline_limit)
    affected_rows = assignment_rows(db, affected_ids)
    baseline_rows = assignment_rows(db, baseline_ids)
    affected_assignment = summarize_assignments(affected_rows, affected_ids, settings)
    baseline_assignment = summarize_assignments(baseline_rows, baseline_ids, settings)
    affected_classification = classification_summary(db, affected_ids)
    baseline_classification = classification_summary(db, baseline_ids)
    affected_localization = localization_summary(db, affected_rows, lang=lang)
    baseline_localization = localization_summary(db, baseline_rows, lang=lang)
    affected_storage = storage_summary(affected_ids)
    ledger = ledger_summary(db, run_ids)
    entity_truth = entity_truth_summary(db, affected_ids)

    anomalies: list[dict[str, Any]] = []
    if affected_assignment["all_ai_assignments_are_suggestions"] and affected_assignment["high_conf_nonproper_expected_normal_count"] > 0:
        anomalies.append(
            {
                "code": "all_ai_assignments_suggestions_with_high_conf_nonproper",
                "status": "confirmed_bug" if affected_assignment["rows_needing_repair_count"] else "repaired",
                "blocker": affected_assignment["rows_needing_repair_count"] > 0,
            }
        )
    if affected_assignment["proper_noun_non_suggestion_count"] > 0:
        anomalies.append({"code": "proper_noun_ai_assignment_not_suggestion", "status": "confirmed_bug", "blocker": True})
    if affected_classification["unknown_or_empty_rate_percent"] > max(80.0, baseline_classification["unknown_or_empty_rate_percent"] + 50.0):
        anomalies.append(
            {
                "code": "classification_unknown_rate_divergence",
                "status": "confirmed_bug" if affected_assignment["rows_needing_repair_count"] else "repaired_or_data_distribution",
                "blocker": affected_assignment["rows_needing_repair_count"] > 0,
                "affected_percent": affected_classification["unknown_or_empty_rate_percent"],
                "baseline_percent": baseline_classification["unknown_or_empty_rate_percent"],
            }
        )
    if affected_storage.get("app_storage_file_missing", 0) > 0 or affected_storage.get("thumbnail_missing", 0) > 0:
        anomalies.append({"code": "storage_or_thumbnail_missing", "status": "suspicious_difference", "blocker": True})
    if affected_localization["localizable_remaining_gap"] > 0:
        anomalies.append({"code": "localization_gap_remaining", "status": "suspicious_difference", "blocker": True})
    if entity_truth["violations_found"] > 0:
        anomalies.append({"code": "ai_only_entity_truth_violation", "status": "confirmed_bug", "blocker": True})

    blocker_count = sum(1 for item in anomalies if item.get("blocker"))
    normal_semantics = (
        affected_assignment["high_conf_nonproper_incorrect_suggestion_count"] == 0
        and affected_assignment["proper_noun_non_suggestion_count"] == 0
        and affected_assignment["high_conf_nonproper_normal_count"] > 0
    )
    return {
        "status": "passed_after_repair" if blocker_count == 0 and normal_semantics else "blocked_anomalies_remaining",
        "run_ids": run_ids,
        "baseline_selection": {
            "method": "latest older non-S3A-M2 media with source='ai_wd' before affected cohort upload window",
            "limit": baseline_limit,
            "media_count": len(baseline_ids),
        },
        "affected_media_count": len(affected_ids),
        "baseline_media_count": len(baseline_ids),
        "affected": {
            "tag_assignment": affected_assignment,
            "classification": affected_classification,
            "localization": affected_localization,
            "storage": affected_storage,
        },
        "baseline": {
            "tag_assignment": baseline_assignment,
            "classification": baseline_classification,
            "localization": baseline_localization,
        },
        "ledger": ledger,
        "entity_truth": entity_truth,
        "anomalies": anomalies,
        "blocker_anomaly_count": blocker_count,
        "normal_ai_tag_semantics_consistent_with_policy": normal_semantics,
        "public_safe": True,
    }


def repair_assignments(
    db: Any,
    rows: list[dict[str, Any]],
    *,
    run_ids: list[int],
    reclassify: bool,
    allow_clip_classification: bool,
) -> dict[str, Any]:
    from app.config import settings
    from app.models import DynamicSourceItem, blombooru_media_tags
    from app.routes.media import update_tag_counts
    from app.services.content_classifier import classify_media

    ledger_rows = []
    touched_tag_ids: set[int] = set()
    touched_media_ids: set[int] = set()
    converted_to_normal = 0
    converted_to_suggestion = 0
    kept_suggestion = 0
    kept_normal = 0
    for row in rows:
        desired = target_suggestion_state(row, settings)
        before = bool(row["is_suggestion"])
        if before == desired:
            if desired:
                kept_suggestion += 1
            else:
                kept_normal += 1
            continue
        db.execute(
            blombooru_media_tags.update()
            .where(blombooru_media_tags.c.media_id == int(row["media_id"]))
            .where(blombooru_media_tags.c.tag_id == int(row["tag_id"]))
            .where(blombooru_media_tags.c.source == AI_SOURCE)
            .values(is_suggestion=desired)
        )
        touched_tag_ids.add(int(row["tag_id"]))
        touched_media_ids.add(int(row["media_id"]))
        if before and not desired:
            converted_to_normal += 1
        elif not before and desired:
            converted_to_suggestion += 1
        ledger_rows.append(
            {
                "media_id": int(row["media_id"]),
                "tag_id": int(row["tag_id"]),
                "category": str(row["category"]),
                "confidence": row.get("confidence"),
                "before_is_suggestion": before,
                "after_is_suggestion": desired,
                "reason": "proper_noun_review_only" if desired and is_proper(row) else "mature_policy_high_conf_nonproper_normal",
            }
        )

    if touched_tag_ids:
        update_tag_counts(db, sorted(touched_tag_ids))

    classification_updates = []
    classification_skip_reason = None
    if reclassify and str(settings.CONTENT_CLASSIFICATION_METHOD or "").lower() == "clip" and not allow_clip_classification:
        classification_skip_reason = "skipped_clip_classification_to_avoid_model_load_or_download"
        reclassify = False

    if reclassify:
        for media_id in sorted({int(row["media_id"]) for row in rows}):
            item = db.query(DynamicSourceItem).filter(DynamicSourceItem.media_id == media_id).first()
            before_status = str(item.classification_status or "") if item else None
            result = classify_media(db, media_id, dry_run=False)
            if item and not result.get("error") and not result.get("skipped"):
                item.classification_status = "classified"
                item.deferred_reason = None
                db.commit()
            classification_updates.append(
                {
                    "media_id": media_id,
                    "before_dynamic_source_classification_status": before_status,
                    "after_content_class": result.get("content_class"),
                    "changed": bool(result.get("changed")),
                    "error": result.get("error"),
                    "skipped": result.get("skipped"),
                }
            )

    db.commit()
    return {
        "run_ids": run_ids,
        "assignments_converted_from_suggestion_to_normal": converted_to_normal,
        "assignments_converted_from_normal_to_suggestion": converted_to_suggestion,
        "assignments_kept_suggestion": kept_suggestion,
        "assignments_kept_normal": kept_normal,
        "assignments_deleted_or_replaced": 0,
        "duplicate_rows_created": 0,
        "touched_media_count": len(touched_media_ids),
        "touched_tag_count": len(touched_tag_ids),
        "classification_rechecked": bool(reclassify),
        "classification_skip_reason": classification_skip_reason,
        "classification_items_rechecked": len(classification_updates),
        "classification_items_changed": sum(1 for row in classification_updates if row.get("changed")),
        "private_ledger_rows": ledger_rows,
        "classification_private_rows": classification_updates,
        "public_safe": True,
    }


def build_incident_public(before: Mapping[str, Any] | None, after: Mapping[str, Any], repair: Mapping[str, Any] | None) -> dict[str, Any]:
    affected_after = after.get("affected", {}).get("tag_assignment", {})
    before_assignment = (before or {}).get("affected", {}).get("tag_assignment", {})
    classification_after = after.get("affected", {}).get("classification", {})
    return {
        "status": "repaired" if after.get("status") == "passed_after_repair" else "blocked",
        "discovered_by": "manual_production_ui_validation",
        "affected_run_ids": after.get("run_ids", []),
        "affected_media_count": after.get("affected_media_count"),
        "assignments_inspected": affected_after.get("assignment_count"),
        "root_cause": "manual_sync_execute_forced_force_suggestions_true_for_all_ai_tags",
        "tests_contract_missed_reason": "previous gates counted ai_tagged media and proper-noun suggestion safety, but did not assert assignment-level normal-vs-suggestion semantics for high-confidence non-proper tags.",
        "before": {
            "all_ai_assignments_are_suggestions": before_assignment.get("all_ai_assignments_are_suggestions"),
            "high_conf_nonproper_expected_normal_count": before_assignment.get("high_conf_nonproper_expected_normal_count"),
            "high_conf_nonproper_incorrect_suggestion_count": before_assignment.get("high_conf_nonproper_incorrect_suggestion_count"),
            "proper_noun_non_suggestion_count": before_assignment.get("proper_noun_non_suggestion_count"),
            "classification_unknown_or_empty_rate_percent": (before or {}).get("affected", {})
            .get("classification", {})
            .get("unknown_or_empty_rate_percent"),
        },
        "repair": {
            key: repair.get(key)
            for key in (
                "assignments_converted_from_suggestion_to_normal",
                "assignments_converted_from_normal_to_suggestion",
                "assignments_kept_suggestion",
                "assignments_kept_normal",
                "assignments_deleted_or_replaced",
                "duplicate_rows_created",
                "classification_rechecked",
                "classification_items_rechecked",
                "classification_items_changed",
            )
        }
        if repair
        else {},
        "after": {
            "all_ai_assignments_are_suggestions": affected_after.get("all_ai_assignments_are_suggestions"),
            "high_conf_nonproper_expected_normal_count": affected_after.get("high_conf_nonproper_expected_normal_count"),
            "high_conf_nonproper_incorrect_suggestion_count": affected_after.get("high_conf_nonproper_incorrect_suggestion_count"),
            "high_conf_nonproper_normal_count": affected_after.get("high_conf_nonproper_normal_count"),
            "proper_noun_non_suggestion_count": affected_after.get("proper_noun_non_suggestion_count"),
            "proper_noun_suggestion_count": affected_after.get("proper_noun_suggestion_count"),
            "classification_content_class_counts": classification_after.get("content_class_counts"),
            "classification_unknown_or_empty_rate_percent": classification_after.get("unknown_or_empty_rate_percent"),
        },
        "entity_truth_violations_found": after.get("entity_truth", {}).get("violations_found", 0),
        "localization_remaining_gap": after.get("affected", {}).get("localization", {}).get("localizable_remaining_gap"),
        "cohort_blocker_anomaly_count": after.get("blocker_anomaly_count"),
        "public_safe": True,
    }


def load_ui_validation_public(path: Path = DEFAULT_UI_VALIDATION_JSON) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "status": "passed" if payload.get("all_samples_passed") else "failed",
        "method": payload.get("method"),
        "computer_use_attempted": bool(payload.get("computer_use_attempted")),
        "computer_use_result": payload.get("computer_use_result"),
        "sample_count": int(payload.get("sample_count") or 0),
        "normal_visible_pass_count": int(payload.get("normal_visible_pass_count") or 0),
        "proper_suggestion_visible_pass_count": int(payload.get("proper_suggestion_visible_pass_count") or 0),
        "any_suggestion_visible_pass_count": int(payload.get("any_suggestion_visible_pass_count") or 0),
        "samples_expect_proper_suggestions": int(payload.get("samples_expect_proper_suggestions") or 0),
        "screenshot_count": len(payload.get("screenshot_artifacts") or []),
        "raw_artifact": f".local_manifests/{PHASE_SLUG}/incident/{path.name}",
        "raw_screenshots_committed": False,
        "raw_ids_private": True,
        "public_safe": True,
    }


def public_markdown(incident: Mapping[str, Any], cohort: Mapping[str, Any]) -> str:
    affected = cohort.get("affected", {})
    baseline = cohort.get("baseline", {})
    ui = incident.get("ui_verification") if isinstance(incident.get("ui_verification"), Mapping) else {}
    return "\n".join(
        [
            "# S3A-M2 AI Tag Assignment Incident",
            "",
            "## Discovery",
            "",
            "- Discovered by manual production UI validation of newly imported S3A-M2 media.",
            "- Visible symptom: high-confidence non-proper AI tags appeared only under suggestion UI grouping.",
            "",
            "## Scope",
            "",
            f"- Affected run IDs: `{incident.get('affected_run_ids')}`.",
            f"- Affected media: `{incident.get('affected_media_count')}`.",
            f"- Assignments inspected: `{incident.get('assignments_inspected')}`.",
            "",
            "## Root Cause",
            "",
            f"- `{incident.get('root_cause')}`.",
            f"- Why missed: {incident.get('tests_contract_missed_reason')}",
            "",
            "## Repair",
            "",
            f"- Status: `{incident.get('status')}`.",
            f"- Before: `{incident.get('before')}`.",
            f"- Repair results: `{incident.get('repair')}`.",
            f"- After: `{incident.get('after')}`.",
            f"- UI verification: `{ui}`.",
            "",
            "## Cohort Self-Audit",
            "",
            f"- Baseline selection: `{cohort.get('baseline_selection')}`.",
            f"- S3A-M2 cohort size: `{cohort.get('affected_media_count')}`; baseline size: `{cohort.get('baseline_media_count')}`.",
            f"- Affected tag assignment: `{affected.get('tag_assignment')}`.",
            f"- Baseline tag assignment: `{baseline.get('tag_assignment')}`.",
            f"- Affected classification: `{affected.get('classification')}`.",
            f"- Baseline classification: `{baseline.get('classification')}`.",
            f"- Affected localization: `{affected.get('localization')}`.",
            f"- Storage/thumbnail: `{affected.get('storage')}`.",
            f"- Ledger: `{cohort.get('ledger')}`.",
            f"- Entity truth: `{cohort.get('entity_truth')}`.",
            f"- Anomalies: `{cohort.get('anomalies')}`.",
            f"- Blocker anomalies remaining: `{cohort.get('blocker_anomaly_count')}`.",
            "",
            "No private filenames, paths, content hashes, prompts, API keys, source URLs, or raw media identifiers are included in this public incident report.",
        ]
    ) + "\n"


def update_public_summary(summary_path: Path, incident: Mapping[str, Any], cohort: Mapping[str, Any]) -> None:
    from scripts.run_s3a_m2_delta_e2e_with_telemetry import (
        git_value,
        public_report_markdown,
        refresh_completion_claims,
        scan_public_output,
        utc_now,
    )

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["generated_at"] = utc_now()
    summary["branch"] = git_value(["branch", "--show-current"])
    summary["head_sha"] = git_value(["rev-parse", "HEAD"])
    readiness = summary.get("readiness")
    if isinstance(readiness, dict):
        readiness["head_sha"] = summary["head_sha"]
    localization = summary.get("localization")
    if isinstance(localization, dict) and "candidate_overflow" not in localization:
        candidate_count = int(localization.get("candidate_count") or 0)
        requested_max = int(localization.get("requested_max_tags") or 0)
        localization["candidate_overflow"] = False
        localization["localization_limit_status"] = (
            "exact_limit_no_overflow" if requested_max > 0 and candidate_count == requested_max else "under_limit"
        )
    ui_validation = load_ui_validation_public()
    if ui_validation:
        incident = dict(incident)
        incident["ui_verification"] = ui_validation
        summary["post_repair_ui_validation"] = ui_validation
    summary["ai_tag_assignment_incident"] = incident
    summary["cohort_self_audit"] = cohort
    refresh_completion_claims(summary)
    markdown = public_report_markdown(summary)
    redaction = scan_public_output(summary, markdown)
    summary["public_redaction"] = redaction
    refresh_completion_claims(summary)
    markdown = public_report_markdown(summary)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=json_default), encoding="utf-8")
    DEFAULT_MAIN_REPORT_MD.write_text(markdown, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", default=None, help="Comma-separated DynamicSyncRun IDs. Defaults to S3A-M2 public summary.")
    parser.add_argument("--baseline-limit", type=int, default=500)
    parser.add_argument("--lang", default="zh-CN")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--before-json", type=Path, default=None, help="Previous read-only audit JSON to embed as pre-repair baseline.")
    parser.add_argument("--repair-summary-json", type=Path, default=None, help="Previous public repair summary JSON to embed during read-only report regeneration.")
    parser.add_argument("--execute-repair", action="store_true", help="Apply assignment repair to affected production DB rows.")
    parser.add_argument("--repair-classification", action="store_true", help="Re-run classification for affected media after assignment repair.")
    parser.add_argument(
        "--allow-clip-classification",
        action="store_true",
        help="Allow repair classification to use CLIP. By default CLIP is skipped to avoid model load/download side effects.",
    )
    parser.add_argument("--approval-phrase", default="")
    parser.add_argument("--write-public-incident-report", action="store_true")
    parser.add_argument("--update-summary", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.runs:
        run_ids = sorted({int(part.strip()) for part in args.runs.split(",") if part.strip()})
    else:
        run_ids = load_run_ids_from_summary(DEFAULT_SUMMARY_JSON)

    before_public = None
    if args.before_json and args.before_json.exists():
        before_public = json.loads(args.before_json.read_text(encoding="utf-8"))
    repair_payload: dict[str, Any] | None = None
    if args.repair_summary_json and args.repair_summary_json.exists():
        repair_payload = json.loads(args.repair_summary_json.read_text(encoding="utf-8"))

    db = open_db_session()
    try:
        before_audit = build_cohort_audit(db, run_ids=run_ids, baseline_limit=args.baseline_limit, lang=args.lang)
        if args.execute_repair:
            if args.approval_phrase != REPAIR_APPROVAL_PHRASE:
                raise SystemExit(f"repair requires exact --approval-phrase {REPAIR_APPROVAL_PHRASE!r}")
            affected_rows = assignment_rows(db, affected_media_ids(db, run_ids))
            repair_payload = repair_assignments(
                db,
                affected_rows,
                run_ids=run_ids,
                reclassify=bool(args.repair_classification),
                allow_clip_classification=bool(args.allow_clip_classification),
            )
        after_audit = build_cohort_audit(db, run_ids=run_ids, baseline_limit=args.baseline_limit, lang=args.lang)
    finally:
        db.close()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    mode = "repair" if args.execute_repair else "diagnosis"
    write_json(args.output_dir / f"{mode}-audit-public.json", after_audit)
    if repair_payload is not None:
        private_rows = repair_payload.pop("private_ledger_rows", [])
        classification_rows = repair_payload.pop("classification_private_rows", [])
        write_json(args.output_dir / "repair-summary-public.json", repair_payload)
        write_jsonl(args.output_dir / "repair-ledger-private.jsonl", private_rows)
        write_jsonl(args.output_dir / "classification-repair-private.jsonl", classification_rows)

    incident = build_incident_public(before_public, after_audit, repair_payload)
    ui_validation = load_ui_validation_public()
    if ui_validation:
        incident = dict(incident)
        incident["ui_verification"] = ui_validation
    write_json(args.output_dir / "incident-summary-public.json", incident)
    if args.write_public_incident_report:
        DEFAULT_INCIDENT_MD.write_text(public_markdown(incident, after_audit), encoding="utf-8")
    if args.update_summary:
        update_public_summary(args.update_summary, incident, after_audit)

    print(json.dumps({"ok": True, "mode": mode, "incident": incident, "cohort_status": after_audit.get("status")}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
