#!/usr/bin/env python3
"""Phase 4.3-A read-only proper-noun signal provenance audit.

Lifecycle: phase-scoped audit runner. It is intentionally read-only against the
database and must not create entities, candidates, assignments, tags, or run any
external lookup. Public reports contain aggregate counts and capped samples only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import and_, func, inspect, text
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app import database  # noqa: E402
from app.enums import (  # noqa: E402
    ContentClassEnum,
    EntityExternalIdentityStatusEnum,
    EntityMetadataSourceEnum,
    EntityReviewStatusEnum,
    EntityTranslationStatusEnum,
    TagCategoryEnum,
)
from app.models import (  # noqa: E402
    Entity,
    EntityAlias,
    EntityEvidence,
    EntityExternalIdentity,
    EntityTranslation,
    ExternalSource,
    Media,
    MediaEntityAssignment,
    MediaEntityCandidate,
    NegativeLookupCache,
    ProviderCache,
    Tag,
    TagTranslation,
    blombooru_media_tags,
)


AI_SOURCES = {"ai_wd"}
MANUAL_SOURCES = {"manual"}
IMPORTED_SOURCES = {"booru_import", "imported", "reverse_search"}
SYSTEM_SOURCES = {"system"}

IDENTITY_CATEGORIES = {"character", "copyright", "artist"}
VISUAL_CONTEXT_CATEGORIES = {"general", "meta"}

SOURCE_KIND_ORDER = ("manual", "imported", "ai", "system", "unknown")
CONTENT_CLASS_ORDER = ("anime", "unknown", "non_anime", "illustration", "null_unclassified")

LOCAL_PATH_PATTERNS = [
    re.compile(r"\b[A-Za-z]:\\"),
    re.compile(r"\\\\"),
    re.compile(r"\bfile://", re.IGNORECASE),
    re.compile(r"(^|\s)/(Users|home|var|tmp|mnt|Volumes|workspace)/"),
]

SECRET_PATTERNS = [
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    re.compile(r"\b(?:sk|key)[_-](?:live|test)[_-]?[A-Za-z0-9]{8,}", re.IGNORECASE),
]

FORBIDDEN_OUTPUT_PREFIXES = (
    "z:\\",
    "\\\\192.168.71.230\\storage",
)

TRUST_TIER_POLICY: dict[str, dict[str, Any]] = {
    "T0": {
        "name": "Manual confirmed entity assignment / manual alias / manual translation",
        "candidate_source": True,
        "evidence_only": False,
        "statistics_only": False,
        "confirmed_assignment": "Only by explicit manual action.",
    },
    "T1": {
        "name": "Trusted external exact metadata with provenance",
        "candidate_source": True,
        "evidence_only": False,
        "statistics_only": False,
        "confirmed_assignment": "Future policy only; not available in Phase 4.3-A.",
    },
    "T2": {
        "name": "Imported/manual locked proper-noun metadata with provenance",
        "candidate_source": True,
        "evidence_only": True,
        "statistics_only": False,
        "confirmed_assignment": "No automatic confirmed assignment by default.",
    },
    "T3": {
        "name": "AI confirmed proper-noun tag",
        "candidate_source": False,
        "evidence_only": True,
        "statistics_only": True,
        "confirmed_assignment": "Blocked by default.",
    },
    "T4": {
        "name": "AI suggestion proper-noun tag",
        "candidate_source": False,
        "evidence_only": False,
        "statistics_only": True,
        "confirmed_assignment": "Blocked.",
    },
    "T5": {
        "name": "General/meta visual co-occurrence",
        "candidate_source": False,
        "evidence_only": False,
        "statistics_only": False,
        "context_only": True,
        "confirmed_assignment": "Blocked.",
    },
}


def enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", str(value))


def normalize_source(source: str | None) -> str:
    return (source or "unknown").strip().lower() or "unknown"


def source_kind(source: str | None) -> str:
    normalized = normalize_source(source)
    if normalized in MANUAL_SOURCES:
        return "manual"
    if normalized in IMPORTED_SOURCES:
        return "imported"
    if normalized in AI_SOURCES or normalized.startswith("ai_"):
        return "ai"
    if normalized in SYSTEM_SOURCES:
        return "system"
    return "unknown"


def content_class_label(value: Any) -> str:
    raw = enum_value(value)
    return raw if raw else "null_unclassified"


def confidence_bucket(confidence: float | None) -> str:
    if confidence is None:
        return "null"
    if confidence < 0.35:
        return "lt_0.35"
    if confidence < 0.65:
        return "0.35_to_lt_0.65"
    if confidence < 0.90:
        return "0.65_to_lt_0.90"
    return "gte_0.90"


def is_uncategorized_proper_noun_like(tag_name: str, category: str | None) -> bool:
    """Conservative heuristic for miscategorized Danbooru-style entity names."""
    if category not in VISUAL_CONTEXT_CATEGORIES:
        return False
    name = tag_name.strip().lower()
    if not name:
        return False
    if re.search(r"_\([a-z0-9_ -]{2,}\)$", name):
        return True
    if re.search(r"^(artist|character|copyright|circle|series|franchise)_", name):
        return True
    if re.search(r"_(artist|character|copyright|circle|series|franchise)$", name):
        return True
    return False


def classify_signal_kind(tag_name: str, category: str | None) -> str:
    if category in IDENTITY_CATEGORIES:
        return category
    if is_uncategorized_proper_noun_like(tag_name, category):
        return "uncategorized_proper_noun_like"
    if category in VISUAL_CONTEXT_CATEGORIES:
        return f"{category}_visual"
    return "unknown_category"


def is_identity_like_signal(signal_kind: str) -> bool:
    return signal_kind in {
        "character",
        "copyright",
        "artist",
        "uncategorized_proper_noun_like",
    }


def classify_trust_tier(
    *,
    tag_category: str | None,
    signal_kind: str,
    source: str | None,
    is_suggestion: bool,
    is_locked: bool,
) -> str:
    """Classify a media-tag signal into Phase 4.3-A trust tiers."""
    if signal_kind in {"general_visual", "meta_visual"}:
        return "T5"

    kind = source_kind(source)
    if kind == "ai":
        return "T4" if is_suggestion else "T3"

    if signal_kind == "uncategorized_proper_noun_like":
        return "T4" if is_suggestion else "T3"

    if tag_category in IDENTITY_CATEGORIES:
        if kind in {"manual", "imported"} and not is_suggestion:
            return "T2"
        if is_locked and not is_suggestion and kind != "ai":
            return "T2"
        if is_suggestion:
            return "T4"
        return "T3"

    return "T5"


def candidate_action_for_tier(tier: str) -> str:
    if tier in {"T0", "T1", "T2"}:
        return "candidate_source"
    if tier == "T3":
        return "weak_evidence_statistics_query_seed"
    if tier == "T4":
        return "statistics_only"
    if tier == "T5":
        return "context_only_not_identity"
    return "blocked"


def entity_type_for_signal(signal_kind: str) -> str:
    if signal_kind == "character":
        return "character"
    if signal_kind == "copyright":
        return "work"
    if signal_kind == "artist":
        return "artist"
    return "unknown"


def assert_public_text_safe(text_value: str) -> None:
    for pattern in LOCAL_PATH_PATTERNS:
        if pattern.search(text_value):
            raise ValueError("Public report appears to contain a local path or file URL")
    for pattern in SECRET_PATTERNS:
        if pattern.search(text_value):
            raise ValueError("Public report appears to contain a secret-shaped token")


def validate_output_path(path: Path) -> None:
    normalized = os.path.normcase(str(path.resolve() if not path.is_absolute() else path))
    for forbidden in FORBIDDEN_OUTPUT_PREFIXES:
        if normalized.startswith(os.path.normcase(forbidden)):
            raise ValueError(f"Refusing to write output under forbidden path: {path}")


def write_text(path: Path, content: str) -> None:
    validate_output_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    validate_output_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _entity_foundation_counts(db: Session) -> dict[str, int]:
    models = [
        Entity,
        EntityAlias,
        EntityExternalIdentity,
        EntityEvidence,
        MediaEntityCandidate,
        MediaEntityAssignment,
        EntityTranslation,
        ExternalSource,
        ProviderCache,
        NegativeLookupCache,
    ]
    tables = set(inspect(db.get_bind()).get_table_names())
    counts = {
        model.__tablename__: (
            int(db.query(func.count(model.id)).scalar() or 0)
            if model.__tablename__ in tables
            else 0
        )
        for model in models
    }
    counts["external_sources_enabled"] = (
        int(db.query(func.count(ExternalSource.id)).filter(ExternalSource.enabled == True).scalar() or 0)
        if ExternalSource.__tablename__ in tables
        else 0
    )
    missing = sorted(model.__tablename__ for model in models if model.__tablename__ not in tables)
    counts["missing_entity_foundation_tables"] = len(missing)
    return counts


def _content_class_filter(content_class: str | None):
    if not content_class or content_class == "all":
        return None
    if content_class in {"null", "unclassified", "null_unclassified"}:
        return Media.content_class.is_(None)
    return Media.content_class == ContentClassEnum(content_class)


def _media_tag_rows(
    db: Session,
    *,
    source_label: str | None,
    content_class: str | None,
) -> Iterable[Any]:
    query = (
        db.query(
            Media.id.label("media_id"),
            Media.content_class.label("content_class"),
            Tag.id.label("tag_id"),
            Tag.name.label("tag_name"),
            Tag.category.label("tag_category"),
            blombooru_media_tags.c.source.label("source"),
            blombooru_media_tags.c.confidence.label("confidence"),
            blombooru_media_tags.c.is_locked.label("is_locked"),
            blombooru_media_tags.c.is_suggestion.label("is_suggestion"),
        )
        .join(blombooru_media_tags, blombooru_media_tags.c.media_id == Media.id)
        .join(Tag, Tag.id == blombooru_media_tags.c.tag_id)
    )

    if source_label:
        query = query.filter(Media.source == source_label)
    class_filter = _content_class_filter(content_class)
    if class_filter is not None:
        query = query.filter(class_filter)

    return query.yield_per(1000)


def _filtered_media_count(db: Session, *, source_label: str | None, content_class: str | None) -> int:
    query = db.query(func.count(Media.id))
    if source_label:
        query = query.filter(Media.source == source_label)
    class_filter = _content_class_filter(content_class)
    if class_filter is not None:
        query = query.filter(class_filter)
    return int(query.scalar() or 0)


def _ordered_counter(counter: Counter, preferred_order: Iterable[str] | None = None) -> dict[str, int]:
    result: dict[str, int] = {}
    seen = set()
    for key in preferred_order or ():
        result[key] = int(counter.get(key, 0))
        seen.add(key)
    for key, count in sorted(counter.items()):
        if key not in seen:
            result[str(key)] = int(count)
    return result


def _summarize_distribution(values: Iterable[int]) -> dict[str, Any]:
    ordered = sorted(int(v) for v in values)
    if not ordered:
        return {"media_count": 0, "max": 0, "p50": 0, "p90": 0, "p95": 0}

    def percentile(p: float) -> int:
        index = min(len(ordered) - 1, int(round((len(ordered) - 1) * p)))
        return ordered[index]

    return {
        "media_count": len(ordered),
        "max": ordered[-1],
        "p50": percentile(0.50),
        "p90": percentile(0.90),
        "p95": percentile(0.95),
    }


def _trusted_anchor_counts(db: Session, proper_noun_manual_locked_rows: int, imported_rows: int) -> dict[str, int]:
    tables = set(inspect(db.get_bind()).get_table_names())
    has_assignments = MediaEntityAssignment.__tablename__ in tables
    has_aliases = EntityAlias.__tablename__ in tables
    has_translations = EntityTranslation.__tablename__ in tables
    has_external = EntityExternalIdentity.__tablename__ in tables
    has_evidence = EntityEvidence.__tablename__ in tables

    confirmed_assignments = (
        int(
            db.query(func.count(MediaEntityAssignment.id))
            .filter(MediaEntityAssignment.review_status == EntityReviewStatusEnum.confirmed)
            .scalar()
            or 0
        )
        if has_assignments
        else 0
    )
    manual_assignments = (
        int(
            db.query(func.count(MediaEntityAssignment.id))
            .filter(
                MediaEntityAssignment.review_status == EntityReviewStatusEnum.confirmed,
                MediaEntityAssignment.source == EntityMetadataSourceEnum.manual,
            )
            .scalar()
            or 0
        )
        if has_assignments
        else 0
    )
    manual_aliases = (
        int(
            db.query(func.count(EntityAlias.id))
            .filter(EntityAlias.source == EntityMetadataSourceEnum.manual)
            .scalar()
            or 0
        )
        if has_aliases
        else 0
    )
    manual_translations = (
        int(
            db.query(func.count(EntityTranslation.id))
            .filter(
                EntityTranslation.source == EntityMetadataSourceEnum.manual,
                EntityTranslation.status == EntityTranslationStatusEnum.confirmed,
            )
            .scalar()
            or 0
        )
        if has_translations
        else 0
    )
    verified_external = (
        int(
            db.query(func.count(EntityExternalIdentity.id))
            .filter(EntityExternalIdentity.identity_status == EntityExternalIdentityStatusEnum.verified)
            .scalar()
            or 0
        )
        if has_external
        else 0
    )
    imported_evidence = (
        int(
            db.query(func.count(EntityEvidence.id))
            .filter(
                EntityEvidence.source_type.in_(["imported", "trusted_external", "external"]),
            )
            .scalar()
            or 0
        )
        if has_evidence
        else 0
    )
    return {
        "confirmed_entity_assignments": confirmed_assignments,
        "manual_confirmed_entity_assignments": manual_assignments,
        "manual_entity_aliases": manual_aliases,
        "manual_confirmed_entity_translations": manual_translations,
        "verified_external_identities": verified_external,
        "imported_or_external_entity_evidence_rows": imported_evidence,
        "manual_or_locked_proper_noun_media_tag_rows": proper_noun_manual_locked_rows,
        "imported_proper_noun_media_tag_rows": imported_rows,
    }


def audit_database(
    db: Session,
    *,
    source_label: str | None = None,
    content_class: str | None = None,
    max_samples: int = 10,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    max_samples = max(0, max_samples)

    entity_counts = _entity_foundation_counts(db)
    total_media = _filtered_media_count(db, source_label=source_label, content_class=content_class)

    total_media_tag_rows = 0
    tag_category_counts: Counter[str] = Counter()
    signal_kind_counts: Counter[str] = Counter()
    trust_tier_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    source_kind_counts: Counter[str] = Counter()
    confidence_counts: Counter[str] = Counter()
    provenance_counts: Counter[str] = Counter()
    entity_like_content_class_counts: Counter[str] = Counter()
    visual_content_class_counts: Counter[str] = Counter()
    entity_like_category_counts: Counter[str] = Counter()
    entity_like_source_tier_counts: Counter[str] = Counter()

    per_tag_rows: dict[int, dict[str, Any]] = {}
    per_media_identity_counts: Counter[int] = Counter()
    per_media_by_tier: dict[str, Counter[int]] = defaultdict(Counter)
    media_content_classes: dict[int, str] = {}

    candidate_by_tier: Counter[str] = Counter()
    candidate_by_group = Counter(
        {
            "T0_T1_T2_default_candidate_sources": 0,
            "T3_ai_confirmed_if_included": 0,
            "T4_ai_suggestions_if_included": 0,
            "T5_visual_context_blocked": 0,
        }
    )
    proper_noun_suggestion_rows = 0
    proper_noun_ai_rows = 0
    proper_noun_manual_locked_rows = 0
    proper_noun_imported_rows = 0
    non_anime_unknown_identity_rows = 0

    for row in _media_tag_rows(db, source_label=source_label, content_class=content_class):
        total_media_tag_rows += 1
        category = enum_value(row.tag_category) or "general"
        cclass = content_class_label(row.content_class)
        signal_kind = classify_signal_kind(row.tag_name, category)
        kind = source_kind(row.source)
        tier = classify_trust_tier(
            tag_category=category,
            signal_kind=signal_kind,
            source=row.source,
            is_suggestion=bool(row.is_suggestion),
            is_locked=bool(row.is_locked),
        )

        tag_category_counts[category] += 1
        signal_kind_counts[signal_kind] += 1
        trust_tier_counts[tier] += 1
        source_counts[normalize_source(row.source)] += 1
        source_kind_counts[kind] += 1
        confidence_counts[confidence_bucket(row.confidence)] += 1
        provenance_counts[
            f"source={normalize_source(row.source)}|kind={kind}|suggestion={bool(row.is_suggestion)}|locked={bool(row.is_locked)}"
        ] += 1

        if is_identity_like_signal(signal_kind):
            entity_like_content_class_counts[cclass] += 1
            entity_like_category_counts[signal_kind] += 1
            entity_like_source_tier_counts[f"{kind}|{tier}"] += 1
            per_media_identity_counts[int(row.media_id)] += 1
            per_media_by_tier[tier][int(row.media_id)] += 1
            media_content_classes[int(row.media_id)] = cclass
            candidate_by_tier[tier] += 1
            if tier in {"T0", "T1", "T2"}:
                candidate_by_group["T0_T1_T2_default_candidate_sources"] += 1
            elif tier == "T3":
                candidate_by_group["T3_ai_confirmed_if_included"] += 1
            elif tier == "T4":
                candidate_by_group["T4_ai_suggestions_if_included"] += 1

            if bool(row.is_suggestion):
                proper_noun_suggestion_rows += 1
            if kind == "ai":
                proper_noun_ai_rows += 1
            if kind in {"manual", "imported"} or (bool(row.is_locked) and kind != "ai"):
                if not bool(row.is_suggestion):
                    proper_noun_manual_locked_rows += 1
            if kind == "imported":
                proper_noun_imported_rows += 1
            if cclass in {"non_anime", "unknown", "null_unclassified"}:
                non_anime_unknown_identity_rows += 1
        else:
            visual_content_class_counts[cclass] += 1
            if tier == "T5":
                candidate_by_group["T5_visual_context_blocked"] += 1

        tag_info = per_tag_rows.setdefault(
            int(row.tag_id),
            {
                "tag_id": int(row.tag_id),
                "name": row.tag_name,
                "category": category,
                "signal_kind": signal_kind,
                "row_count": 0,
                "source_kinds": Counter(),
                "sources": Counter(),
                "trust_tiers": Counter(),
                "content_classes": Counter(),
                "suggestion_rows": 0,
                "locked_rows": 0,
            },
        )
        tag_info["row_count"] += 1
        tag_info["source_kinds"][kind] += 1
        tag_info["sources"][normalize_source(row.source)] += 1
        tag_info["trust_tiers"][tier] += 1
        tag_info["content_classes"][cclass] += 1
        if bool(row.is_suggestion):
            tag_info["suggestion_rows"] += 1
        if bool(row.is_locked):
            tag_info["locked_rows"] += 1

    identity_tags = [
        info for info in per_tag_rows.values()
        if is_identity_like_signal(info["signal_kind"])
    ]
    proper_noun_tags_sourced_only_from_ai = sum(
        1
        for info in identity_tags
        if set(info["source_kinds"].keys()) == {"ai"}
    )

    def public_tag_info(info: dict[str, Any]) -> dict[str, Any]:
        source_kinds = sorted(info["source_kinds"].keys(), key=lambda x: SOURCE_KIND_ORDER.index(x) if x in SOURCE_KIND_ORDER else 99)
        return {
            "tag": info["name"],
            "category": info["category"],
            "signal_kind": info["signal_kind"],
            "media_tag_rows": int(info["row_count"]),
            "source_kinds": source_kinds,
            "ai_only": source_kinds == ["ai"],
            "suggestion_rows": int(info["suggestion_rows"]),
            "locked_rows": int(info["locked_rows"]),
        }

    top_identity_tags = [
        public_tag_info(info)
        for info in sorted(identity_tags, key=lambda x: (-x["row_count"], x["name"]))[:max_samples]
    ]

    media_many_identity = [
        {
            "media_id": int(media_id),
            "content_class": media_content_classes.get(media_id, "null_unclassified"),
            "proper_noun_signal_rows": int(count),
        }
        for media_id, count in per_media_identity_counts.most_common(max_samples)
    ]

    candidate_distribution = {
        tier: _summarize_distribution(counter.values())
        for tier, counter in sorted(per_media_by_tier.items())
    }
    candidate_distribution["all_identity_like"] = _summarize_distribution(per_media_identity_counts.values())

    trusted_anchors = _trusted_anchor_counts(
        db,
        proper_noun_manual_locked_rows=proper_noun_manual_locked_rows,
        imported_rows=proper_noun_imported_rows,
    )

    finished_at = datetime.now(timezone.utc)
    report = {
        "phase": "4.3-A",
        "title": "Proper-noun signal provenance audit and trust policy",
        "generated_at": finished_at.isoformat(),
        "audit_scope": {
            "source_label": source_label,
            "content_class": content_class or "all",
            "max_samples": max_samples,
            "database_access": "read_only",
            "external_calls": False,
            "candidate_writes": False,
            "assignment_writes": False,
            "entity_writes": False,
        },
        "runtime": {
            "repo_root": str(REPO_ROOT),
            "python_executable": sys.executable,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
        },
        "entity_foundation_counts": entity_counts,
        "trusted_anchor_counts": trusted_anchors,
        "media_tag_signal_counts": {
            "filtered_media_count": total_media,
            "media_tag_rows": total_media_tag_rows,
            "proper_noun_or_entity_like_rows": int(sum(entity_like_category_counts.values())),
            "visual_context_rows": int(sum(visual_content_class_counts.values())),
            "distinct_proper_noun_or_entity_like_tags": len(identity_tags),
            "proper_noun_tags_sourced_only_from_ai": int(proper_noun_tags_sourced_only_from_ai),
            "proper_noun_ai_media_tag_rows": int(proper_noun_ai_rows),
            "proper_noun_suggestion_rows": int(proper_noun_suggestion_rows),
            "proper_noun_non_anime_unknown_or_unclassified_rows": int(non_anime_unknown_identity_rows),
        },
        "tag_category_distribution": _ordered_counter(tag_category_counts, ["character", "copyright", "artist", "general", "meta"]),
        "signal_kind_distribution": _ordered_counter(
            signal_kind_counts,
            ["character", "copyright", "artist", "uncategorized_proper_noun_like", "general_visual", "meta_visual"],
        ),
        "source_distribution": _ordered_counter(source_counts),
        "source_kind_distribution": _ordered_counter(source_kind_counts, SOURCE_KIND_ORDER),
        "confidence_distribution": _ordered_counter(
            confidence_counts,
            ["null", "lt_0.35", "0.35_to_lt_0.65", "0.65_to_lt_0.90", "gte_0.90"],
        ),
        "trust_tier_distribution": _ordered_counter(trust_tier_counts, ["T0", "T1", "T2", "T3", "T4", "T5"]),
        "proper_noun_content_class_distribution": _ordered_counter(entity_like_content_class_counts, CONTENT_CLASS_ORDER),
        "visual_context_content_class_distribution": _ordered_counter(visual_content_class_counts, CONTENT_CLASS_ORDER),
        "proper_noun_signal_by_kind": _ordered_counter(
            entity_like_category_counts,
            ["character", "copyright", "artist", "uncategorized_proper_noun_like"],
        ),
        "proper_noun_source_tier_distribution": _ordered_counter(entity_like_source_tier_counts),
        "provenance_distribution": _ordered_counter(provenance_counts),
        "candidate_generation_simulation": {
            "no_writes_performed": True,
            "estimated_candidate_signal_rows_by_tier": _ordered_counter(candidate_by_tier, ["T0", "T1", "T2", "T3", "T4", "T5"]),
            "estimated_candidate_signal_rows_by_policy_group": _ordered_counter(candidate_by_group),
            "per_media_candidate_signal_distribution": candidate_distribution,
            "future_phase_4_3b_hard_caps": {
                "max_candidates_per_media": 5,
                "max_total_candidates_per_run": 500,
                "default_source_tiers": ["T0", "T1", "T2"],
                "dry_run_first": True,
                "execute_confirmation_required": True,
                "block_t3_by_default_until_user_approval": True,
                "block_t4_by_default": True,
                "block_t5_as_identity_source": True,
            },
        },
        "trust_tier_policy": TRUST_TIER_POLICY,
        "risk_indicators": {
            "proper_noun_tags_sourced_only_from_ai": int(proper_noun_tags_sourced_only_from_ai),
            "proper_noun_ai_media_tag_rows": int(proper_noun_ai_rows),
            "proper_noun_suggestion_rows": int(proper_noun_suggestion_rows),
            "proper_noun_non_anime_unknown_or_unclassified_rows": int(non_anime_unknown_identity_rows),
            "top_identity_tags": top_identity_tags,
            "media_with_many_proper_noun_signals": media_many_identity,
        },
        "recommendation": {
            "phase_4_3b": "defer_guarded_candidate_generation_from_ai_t3_by_default",
            "default_candidate_tiers": ["T0", "T1", "T2"],
            "t3_ai_confirmed": "weak_evidence_statistics_or_future_query_seed_only_by_default",
            "t4_ai_suggestion": "statistics_only",
            "t5_general_meta": "context_only_not_identity",
            "proceed_condition": "Proceed with 4.3-B only for T0/T1/T2 dry-run candidate generation, or after explicit approval to include T3 with caps.",
        },
        "explicit_safety_confirmation": {
            "db_writes": False,
            "external_network_calls": False,
            "entity_candidate_creation": False,
            "entity_assignment_creation": False,
            "entity_creation": False,
            "db_import": False,
            "classification": False,
            "ai_tagging": False,
            "localization": False,
            "source_icloud_mutation": False,
            "app_managed_storage_mutation": False,
            "entity_resolver_execution": False,
            "similarity_or_clustering": False,
        },
    }
    return report


def render_public_markdown(report: dict[str, Any]) -> str:
    counts = report["media_tag_signal_counts"]
    anchors = report["trusted_anchor_counts"]
    simulation = report["candidate_generation_simulation"]
    recommendation = report["recommendation"]

    lines = [
        "# Phase 4.3-A - Proper-Noun Signal Provenance Audit and Trust Policy",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Summary",
        "",
        "This read-only audit inspects current local DB tag/entity metadata and defines the default trust policy for future entity candidate generation. It makes no candidate, assignment, entity, import, classification, AI-tagging, localization, Entity Resolver, similarity, storage, or source/iCloud writes.",
        "",
        "Existing AI-generated character/copyright/artist/proper-noun tags are weak identity evidence by default. General/meta visual tags remain useful visual descriptors, but they are not identity signals.",
        "",
        "## Audit Scope",
        "",
        f"- Source label filter: `{report['audit_scope']['source_label'] or 'all'}`",
        f"- Content-class filter: `{report['audit_scope']['content_class']}`",
        f"- Filtered media count: `{counts['filtered_media_count']}`",
        f"- Media-tag rows inspected: `{counts['media_tag_rows']}`",
        "- DB access: read-only session; no `commit()` path in the audit runner.",
        "- External calls: none.",
        "",
        "## Existing Entity Foundation State",
        "",
    ]
    for table, value in report["entity_foundation_counts"].items():
        lines.append(f"- `{table}`: `{value}`")
    if report["entity_foundation_counts"].get("missing_entity_foundation_tables", 0):
        lines.append("")
        lines.append(
            "Note: entity foundation tables are absent in the audited local DB. "
            "The audit did not run migrations because Phase 4.3-A is read-only."
        )

    lines.extend([
        "",
        "## Proper-Noun / Entity-Like Signal Counts",
        "",
        f"- Proper-noun/entity-like media-tag rows: `{counts['proper_noun_or_entity_like_rows']}`",
        f"- Distinct proper-noun/entity-like tags: `{counts['distinct_proper_noun_or_entity_like_tags']}`",
        f"- Proper-noun tags sourced only from AI: `{counts['proper_noun_tags_sourced_only_from_ai']}`",
        f"- Proper-noun AI media-tag rows: `{counts['proper_noun_ai_media_tag_rows']}`",
        f"- Proper-noun suggestion rows: `{counts['proper_noun_suggestion_rows']}`",
        f"- Proper-noun rows on `non_anime` / `unknown` / unclassified media: `{counts['proper_noun_non_anime_unknown_or_unclassified_rows']}`",
        f"- General/meta visual context rows: `{counts['visual_context_rows']}`",
        "",
        "## Tag Category Distribution",
        "",
    ])
    for key, value in report["tag_category_distribution"].items():
        lines.append(f"- `{key}`: `{value}`")

    lines.extend(["", "## Signal Kind Distribution", ""])
    for key, value in report["signal_kind_distribution"].items():
        lines.append(f"- `{key}`: `{value}`")

    lines.extend(["", "## Provenance Distribution", ""])
    for key, value in report["source_kind_distribution"].items():
        lines.append(f"- `{key}`: `{value}`")

    lines.extend(["", "## Confidence Distribution", ""])
    for key, value in report["confidence_distribution"].items():
        lines.append(f"- `{key}`: `{value}`")

    lines.extend(["", "## Proper-Noun Content-Class Distribution", ""])
    for key, value in report["proper_noun_content_class_distribution"].items():
        lines.append(f"- `{key}`: `{value}`")

    lines.extend(["", "## Trusted Anchors", ""])
    for key, value in anchors.items():
        lines.append(f"- `{key}`: `{value}`")

    lines.extend(["", "## Trust-Tier Policy", ""])
    for tier, policy in report["trust_tier_policy"].items():
        lines.append(
            f"- `{tier}` - {policy['name']}: candidate_source=`{policy.get('candidate_source', False)}`, "
            f"statistics_only=`{policy.get('statistics_only', False)}`, "
            f"confirmed_assignment=`{policy.get('confirmed_assignment', 'Blocked')}`"
        )

    lines.extend(["", "## Candidate-Generation Simulation", ""])
    for key, value in simulation["estimated_candidate_signal_rows_by_policy_group"].items():
        lines.append(f"- `{key}`: `{value}`")

    dist = simulation["per_media_candidate_signal_distribution"].get("all_identity_like", {})
    lines.extend([
        f"- Per-media identity-like distribution: media_count=`{dist.get('media_count', 0)}`, max=`{dist.get('max', 0)}`, p50=`{dist.get('p50', 0)}`, p90=`{dist.get('p90', 0)}`, p95=`{dist.get('p95', 0)}`",
        "",
        "Recommended future Phase 4.3-B caps:",
    ])
    for key, value in simulation["future_phase_4_3b_hard_caps"].items():
        lines.append(f"- `{key}`: `{value}`")

    lines.extend(["", "## Risk Indicators", ""])
    top_tags = report["risk_indicators"]["top_identity_tags"]
    if top_tags:
        lines.append("Top identity-like tags by media-tag row count, capped:")
        for item in top_tags:
            lines.append(
                f"- `{item['tag']}` ({item['category']}): rows=`{item['media_tag_rows']}`, "
                f"source_kinds=`{','.join(item['source_kinds'])}`, ai_only=`{item['ai_only']}`, "
                f"suggestions=`{item['suggestion_rows']}`, locked=`{item['locked_rows']}`"
            )
    else:
        lines.append("- No identity-like tags found.")

    media_many = report["risk_indicators"]["media_with_many_proper_noun_signals"]
    if media_many:
        lines.append("")
        lines.append("Media with many proper-noun signals, capped:")
        for item in media_many:
            lines.append(
                f"- media_id=`{item['media_id']}`, content_class=`{item['content_class']}`, "
                f"proper_noun_signal_rows=`{item['proper_noun_signal_rows']}`"
            )

    lines.extend([
        "",
        "## Recommendation",
        "",
        f"- Phase 4.3-B recommendation: `{recommendation['phase_4_3b']}`",
        f"- Default candidate tiers: `{', '.join(recommendation['default_candidate_tiers'])}`",
        f"- T3 AI confirmed proper-noun tags: `{recommendation['t3_ai_confirmed']}`",
        f"- T4 AI suggestions: `{recommendation['t4_ai_suggestion']}`",
        f"- T5 general/meta visual tags: `{recommendation['t5_general_meta']}`",
        f"- Proceed condition: {recommendation['proceed_condition']}",
        "",
        "## Explicit Safety Confirmation",
        "",
    ])
    for key, value in report["explicit_safety_confirmation"].items():
        lines.append(f"- `{key}`: `{value}`")

    return "\n".join(lines) + "\n"


def make_public_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "phase": report["phase"],
        "title": report["title"],
        "generated_at": report["generated_at"],
        "audit_scope": report["audit_scope"],
        "entity_foundation_counts": report["entity_foundation_counts"],
        "trusted_anchor_counts": report["trusted_anchor_counts"],
        "media_tag_signal_counts": report["media_tag_signal_counts"],
        "tag_category_distribution": report["tag_category_distribution"],
        "signal_kind_distribution": report["signal_kind_distribution"],
        "source_kind_distribution": report["source_kind_distribution"],
        "confidence_distribution": report["confidence_distribution"],
        "trust_tier_distribution": report["trust_tier_distribution"],
        "proper_noun_content_class_distribution": report["proper_noun_content_class_distribution"],
        "candidate_generation_simulation": report["candidate_generation_simulation"],
        "trust_tier_policy": report["trust_tier_policy"],
        "risk_indicators": report["risk_indicators"],
        "recommendation": report["recommendation"],
        "explicit_safety_confirmation": report["explicit_safety_confirmation"],
    }


def set_read_only_transaction(db: Session) -> None:
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        db.execute(text("SET TRANSACTION READ ONLY"))


def run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-label", default=None, help="Optional exact Media.source/source label filter.")
    parser.add_argument(
        "--content-class",
        default="all",
        choices=["all", "anime", "unknown", "non_anime", "illustration", "null", "unclassified", "null_unclassified"],
        help="Optional Media.content_class filter. Default: all.",
    )
    parser.add_argument("--report-json", type=Path, default=None, help="Write public aggregate summary JSON.")
    parser.add_argument("--report-md", type=Path, default=None, help="Write public aggregate Markdown report.")
    parser.add_argument("--local-details-json", type=Path, default=None, help="Write capped local details JSON artifact.")
    parser.add_argument("--max-samples", type=int, default=10, help="Max top-tag/media samples in public and local outputs.")
    parser.add_argument("--strict", action="store_true", help="Fail if public output contains local paths/secrets or DB counts change.")
    args = parser.parse_args(argv)

    database.init_engine()
    if database.SessionLocal is None:
        raise RuntimeError("Database not initialized. Complete onboarding or set the expected DB environment first.")

    db = database.SessionLocal()
    try:
        before_counts = _entity_foundation_counts(db)
        set_read_only_transaction(db)
        report = audit_database(
            db,
            source_label=args.source_label,
            content_class=args.content_class,
            max_samples=args.max_samples,
        )
        after_counts = _entity_foundation_counts(db)
        if args.strict and before_counts != after_counts:
            raise RuntimeError("Entity foundation counts changed during read-only audit")

        public_summary = make_public_summary(report)
        public_md = render_public_markdown(report)
        if args.strict:
            assert_public_text_safe(json.dumps(public_summary, ensure_ascii=False))
            assert_public_text_safe(public_md)

        if args.report_json:
            write_json(args.report_json, public_summary)
        if args.report_md:
            write_text(args.report_md, public_md)
        if args.local_details_json:
            write_json(args.local_details_json, report)

        if not args.report_json and not args.report_md and not args.local_details_json:
            print(json.dumps(public_summary, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    raise SystemExit(run_cli())
