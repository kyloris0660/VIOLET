"""Read-only aggregate audit for manual-sync non-target AI/localization leakage."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"

import sys

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import settings  # noqa: E402
from app import database as app_database  # noqa: E402
from app.models import (  # noqa: E402
    DynamicSourceItem,
    DynamicSyncRun,
    DynamicSyncRunItem,
    Media,
    Tag,
    TagTranslation,
    blombooru_media_tags,
)


DEFAULT_OUTPUT_DIR = ROOT / ".local_manifests" / "s3a_m2_delta_e2e" / "non_target_ai_audit"
TARGET_CLASSES = {"anime", "illustration"}
CONFIRMED_NON_TARGET_CLASSES = {"non_anime"}
UNKNOWN_OR_UNCERTAIN_CLASSES = {"", "none", "null", "unknown", "unclassified", "uncertain"}


def _content_class(value: Any) -> str:
    value = getattr(value, "value", value)
    return str(value or "unclassified").strip().lower()


def _content_class_group(value: Any) -> str:
    content_class = _content_class(value)
    if content_class in TARGET_CLASSES:
        return "target"
    if content_class in CONFIRMED_NON_TARGET_CLASSES:
        return "confirmed_non_target"
    return "unknown_or_uncertain"


def _parse_run_ids(raw: str) -> list[int]:
    values: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        values.append(int(chunk))
    return values


def audit(run_ids: list[int]) -> dict[str, Any]:
    if app_database.SessionLocal is None:
        app_database.init_engine()
    if app_database.SessionLocal is None:
        raise RuntimeError("database_session_not_initialized")
    db = app_database.SessionLocal()
    try:
        runs = (
            db.query(DynamicSyncRun)
            .filter(DynamicSyncRun.id.in_(run_ids))
            .order_by(DynamicSyncRun.id.asc())
            .all()
        )
        media_rows = (
            db.query(Media.id, Media.content_class, DynamicSyncRunItem.sync_run_id)
            .join(DynamicSyncRunItem, DynamicSyncRunItem.media_id == Media.id)
            .filter(DynamicSyncRunItem.sync_run_id.in_(run_ids))
            .filter(DynamicSyncRunItem.media_id.isnot(None))
            .distinct()
            .all()
        )
        media_ids = sorted({int(row.id) for row in media_rows})
        class_by_media = {int(row.id): _content_class(row.content_class) for row in media_rows}
        run_media_counts: dict[str, Counter[str]] = {}
        for row in media_rows:
            run_media_counts.setdefault(str(row.sync_run_id), Counter())[_content_class(row.content_class)] += 1

        run_item_rows = (
            db.query(
                DynamicSyncRunItem.sync_run_id,
                DynamicSyncRunItem.item_state,
                DynamicSyncRunItem.reason,
                func.count().label("count"),
            )
            .filter(DynamicSyncRunItem.sync_run_id.in_(run_ids))
            .group_by(DynamicSyncRunItem.sync_run_id, DynamicSyncRunItem.item_state, DynamicSyncRunItem.reason)
            .all()
        )
        run_item_state_counts: dict[str, Counter[str]] = {}
        run_item_reason_counts: dict[str, Counter[str]] = {}
        for row in run_item_rows:
            run_id = str(row.sync_run_id)
            state = str(row.item_state or "unknown")
            reason = str(row.reason or "none")
            run_item_state_counts.setdefault(run_id, Counter())[state] += int(row.count or 0)
            run_item_reason_counts.setdefault(run_id, Counter())[reason] += int(row.count or 0)

        source_item_stage_rows = (
            db.query(
                DynamicSyncRunItem.sync_run_id,
                Media.content_class,
                DynamicSourceItem.classification_status,
                DynamicSourceItem.ai_tagging_status,
                DynamicSourceItem.localization_status,
                func.count(func.distinct(DynamicSourceItem.id)).label("count"),
            )
            .join(DynamicSourceItem, DynamicSyncRunItem.source_item_id == DynamicSourceItem.id)
            .outerjoin(Media, DynamicSyncRunItem.media_id == Media.id)
            .filter(DynamicSyncRunItem.sync_run_id.in_(run_ids))
            .group_by(
                DynamicSyncRunItem.sync_run_id,
                Media.content_class,
                DynamicSourceItem.classification_status,
                DynamicSourceItem.ai_tagging_status,
                DynamicSourceItem.localization_status,
            )
            .all()
        )
        source_item_stage_counts: dict[str, Counter[str]] = {}
        for row in source_item_stage_rows:
            key = ":".join(
                [
                    _content_class(row.content_class),
                    str(row.classification_status or "unknown"),
                    str(row.ai_tagging_status or "unknown"),
                    str(row.localization_status or "unknown"),
                ]
            )
            source_item_stage_counts.setdefault(str(row.sync_run_id), Counter())[key] += int(row.count or 0)

        assignments = []
        if media_ids:
            assignments = (
                db.query(
                    blombooru_media_tags.c.media_id,
                    Tag.category,
                    blombooru_media_tags.c.is_suggestion,
                    func.count().label("count"),
                )
                .join(Tag, Tag.id == blombooru_media_tags.c.tag_id)
                .filter(blombooru_media_tags.c.media_id.in_(media_ids))
                .filter(blombooru_media_tags.c.source == "ai_wd")
                .group_by(
                    blombooru_media_tags.c.media_id,
                    Tag.category,
                    blombooru_media_tags.c.is_suggestion,
                )
                .all()
            )

        assignment_counts: Counter[str] = Counter()
        confirmed_non_target_media_with_ai: set[int] = set()
        unknown_or_uncertain_media_with_ai: set[int] = set()
        unclassified_media_with_ai: set[int] = set()
        target_media_with_ai: set[int] = set()
        for row in assignments:
            cls = class_by_media.get(int(row.media_id), "unclassified")
            class_group = _content_class_group(cls)
            category = getattr(row.category, "value", row.category)
            suggestion = "suggestion" if row.is_suggestion else "normal"
            count = int(row.count or 0)
            assignment_counts[f"{cls}:{category}:{suggestion}"] += count
            if class_group == "target":
                target_media_with_ai.add(int(row.media_id))
            elif class_group == "confirmed_non_target":
                confirmed_non_target_media_with_ai.add(int(row.media_id))
            else:
                unknown_or_uncertain_media_with_ai.add(int(row.media_id))
                if cls == "unclassified":
                    unclassified_media_with_ai.add(int(row.media_id))

        confirmed_non_target_tag_rows = []
        if confirmed_non_target_media_with_ai:
            confirmed_non_target_tag_rows = (
                db.query(Tag.id, Tag.category)
                .join(blombooru_media_tags, Tag.id == blombooru_media_tags.c.tag_id)
                .filter(blombooru_media_tags.c.media_id.in_(sorted(confirmed_non_target_media_with_ai)))
                .filter(blombooru_media_tags.c.source == "ai_wd")
                .distinct()
                .all()
            )
        translated_confirmed_non_target_distinct_tags = 0
        if confirmed_non_target_tag_rows:
            translated_confirmed_non_target_distinct_tags = int(
                db.query(func.count(func.distinct(TagTranslation.canonical_name)))
                .join(Tag, Tag.name == TagTranslation.canonical_name)
                .filter(Tag.id.in_([int(row.id) for row in confirmed_non_target_tag_rows]))
                .filter(TagTranslation.language == "zh-CN")
                .filter(TagTranslation.status != "rejected")
                .scalar()
                or 0
            )

        confirmed_non_target_assignment_count = int(
            sum(value for key, value in assignment_counts.items() if key.split(":", 1)[0] in CONFIRMED_NON_TARGET_CLASSES)
        )
        unknown_or_uncertain_assignment_count = int(
            sum(value for key, value in assignment_counts.items() if key.split(":", 1)[0] in UNKNOWN_OR_UNCERTAIN_CLASSES)
        )

        return {
            "audit": "manual_sync_non_target_ai_localization_v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "violet_env": settings.VIOLET_ENV,
            "db_name": settings.DB_NAME,
            "run_ids": run_ids,
            "runs_found": [
                {
                    "run_id": int(run.id),
                    "status": run.status,
                    "run_type": run.run_type,
                    "mode": run.mode,
                    "total_seen": int(run.total_seen or 0),
                    "new_items": int(run.new_items or 0),
                    "failed_items": int(run.failed_items or 0),
                }
                for run in runs
            ],
            "target_content_classes": sorted(TARGET_CLASSES),
            "confirmed_non_target_content_classes": sorted(CONFIRMED_NON_TARGET_CLASSES),
            "unknown_or_uncertain_content_classes": sorted(value for value in UNKNOWN_OR_UNCERTAIN_CLASSES if value),
            "media_count": len(media_ids),
            "media_by_content_class": dict(sorted(Counter(class_by_media.values()).items())),
            "media_by_run_and_content_class": {
                run_id: dict(sorted(counter.items())) for run_id, counter in sorted(run_media_counts.items())
            },
            "run_item_state_counts": {
                run_id: dict(sorted(counter.items())) for run_id, counter in sorted(run_item_state_counts.items())
            },
            "run_item_reason_counts": {
                run_id: dict(sorted(counter.items())) for run_id, counter in sorted(run_item_reason_counts.items())
            },
            "source_item_stage_counts_by_run_content_class": {
                run_id: dict(sorted(counter.items())) for run_id, counter in sorted(source_item_stage_counts.items())
            },
            "ai_wd_assignment_count": int(sum(assignment_counts.values())),
            "ai_wd_assignments_by_content_class_category_suggestion": dict(sorted(assignment_counts.items())),
            "target_media_with_ai_count": len(target_media_with_ai),
            "confirmed_non_target_media_with_ai_count": len(confirmed_non_target_media_with_ai),
            "confirmed_non_target_ai_wd_assignment_count": confirmed_non_target_assignment_count,
            "confirmed_non_target_distinct_ai_wd_tags": len(confirmed_non_target_tag_rows),
            "confirmed_non_target_distinct_ai_wd_tags_with_zh_cn_translation": translated_confirmed_non_target_distinct_tags,
            "unknown_or_uncertain_media_with_ai_count": len(unknown_or_uncertain_media_with_ai),
            "unknown_or_uncertain_ai_wd_assignment_count": unknown_or_uncertain_assignment_count,
            "unclassified_media_with_ai_count": len(unclassified_media_with_ai),
            "non_target_media_with_ai_count": len(confirmed_non_target_media_with_ai),
            "non_target_ai_wd_assignment_count": confirmed_non_target_assignment_count,
            "repair_policy": {
                "default_repair_scope": "confirmed_non_target_only",
                "unknown_or_uncertain_default_action": "do_not_remove_ai_tags_by_default",
                "requires_project_owner_approval_before_db_repair": True,
            },
            "public_safe": True,
            "private_paths_printed": False,
            "content_hashes_printed": False,
            "db_writes_performed": False,
        }
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-ids", required=True, help="Comma-separated DynamicSyncRun ids, e.g. 7,8,13")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()

    run_ids = _parse_run_ids(args.run_ids)
    if not run_ids:
        raise SystemExit("--run-ids must contain at least one id")
    output_dir = Path(args.output_dir).resolve()
    approved_root = DEFAULT_OUTPUT_DIR.resolve().parent
    try:
        output_dir.relative_to(approved_root)
    except ValueError as exc:
        raise SystemExit("output-dir must stay under .local_manifests/s3a_m2_delta_e2e") from exc
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = audit(run_ids)
    output_path = output_dir / f"manual-sync-non-target-ai-audit-runs-{'-'.join(map(str, run_ids))}.json"
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": "ok", "output": str(output_path), "summary": payload}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
