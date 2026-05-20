"""Classification-first E2E workflow planning helpers.

Phase 3.8b intentionally implements read-only planning and dry-run reporting
only.  The helpers in this module define the reusable service contract that a
future execute workflow can call after explicit approval.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..enums import ContentClassEnum, TagCategoryEnum
from ..models import (
    AITagJob,
    ClassificationJob,
    Media,
    Tag,
    TagTranslation,
    TagTranslationJob,
    blombooru_media_tags,
)


DEFAULT_SOURCE_LABEL = "violet:tier1000:phase3.5"
AI_SOURCE = "ai_wd"
ZH_LANG = "zh-CN"

ELIGIBLE_CONTENT_CLASSES = ("anime", "unknown")
INELIGIBLE_CONTENT_CLASSES = ("non_anime", "illustration", "unclassified", "failed", "error")
LOCALIZABLE_CATEGORIES = ("general", "meta")
PROPER_NOUN_CATEGORIES = ("character", "copyright", "artist")
ACTIVE_JOB_STATUSES = ("pending", "running", "cancelling")
NULL_POLICY_HARD_FAIL = "hard_fail"
NULL_POLICY_TREAT_AS_UNKNOWN = "treat_as_unknown"

PUBLIC_PATH_REDACTION = "<redacted_path>"
SECRET_REDACTION = "<redacted_secret>"

URL_RE = re.compile(r"[a-z][a-z0-9+.-]*://[^\s\"'<>]+", re.IGNORECASE)
FILE_URI_RE = re.compile(r"file://(?:(?![\r\n\"<>|]).)+", re.IGNORECASE)
WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"(?i)(?<![A-Z0-9_])[A-Z]:[\\/](?:(?![\r\n\"<>|]).)+")
UNC_PATH_RE = re.compile(r"\\\\(?:(?![\r\n\"<>|]).)+")
POSIX_ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])/(?!/)(?=\S)(?:(?![\r\n\"<>|]).)+")
SECRET_TOKEN_RE = re.compile(r"Bearer\s+[A-Za-z0-9._~+\-/]+=*", re.IGNORECASE)
API_KEY_RE = re.compile(r"(sk-|key-)[A-Za-z0-9_\-]{8,}")
URL_PASSWORD_RE = re.compile(r"([a-z0-9+.-]+://[^:\s/@]+:)(?!\*\*\*@)([^@\s]+)(@)", re.IGNORECASE)


class WorkflowContractError(RuntimeError):
    """Raised when a dry-run contract or future execute gate fails."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def enum_value(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        return str(value.value)
    text = str(value)
    return text.split(".", 1)[-1] if "." in text else text


def _protect_urls(text: str) -> tuple[str, dict[str, str]]:
    protected: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        if match.group(0).lower().startswith("file://"):
            return match.group(0)
        token = f"__VIOLET_URL_{len(protected)}__"
        protected[token] = match.group(0)
        return token

    return URL_RE.sub(replace, text), protected


def _restore_urls(text: str, protected: Mapping[str, str]) -> str:
    restored = text
    for token, url in protected.items():
        restored = restored.replace(token, url)
    return restored


def sanitize_public_text(value: str) -> str:
    text = str(value)
    text = SECRET_TOKEN_RE.sub(f"Bearer {SECRET_REDACTION}", text)
    text = API_KEY_RE.sub(r"\1***", text)
    text = URL_PASSWORD_RE.sub(r"\1***\3", text)
    text = FILE_URI_RE.sub(PUBLIC_PATH_REDACTION, text)
    protected_text, protected_urls = _protect_urls(text)
    protected_text = WINDOWS_ABSOLUTE_PATH_RE.sub(PUBLIC_PATH_REDACTION, protected_text)
    protected_text = UNC_PATH_RE.sub(PUBLIC_PATH_REDACTION, protected_text)
    protected_text = POSIX_ABSOLUTE_PATH_RE.sub(PUBLIC_PATH_REDACTION, protected_text)
    return _restore_urls(protected_text, protected_urls)


def sanitize_public_obj(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_public_text(value)
    if isinstance(value, list):
        return [sanitize_public_obj(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_public_obj(item) for item in value]
    if isinstance(value, dict):
        return {str(key): sanitize_public_obj(item) for key, item in value.items()}
    return value


def find_privacy_leaks(value: Any) -> list[str]:
    """Return human-readable leak categories found in serialized public output."""

    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    protected_text, _ = _protect_urls(text)
    leaks: list[str] = []
    if (
        FILE_URI_RE.search(text)
        or WINDOWS_ABSOLUTE_PATH_RE.search(protected_text)
        or UNC_PATH_RE.search(protected_text)
        or POSIX_ABSOLUTE_PATH_RE.search(protected_text)
    ):
        leaks.append("absolute_path")
    if SECRET_TOKEN_RE.search(text) or API_KEY_RE.search(text):
        leaks.append("secret_token")
    for match in URL_PASSWORD_RE.finditer(text):
        if match.group(2) != "***":
            leaks.append("url_password")
            break
    return sorted(set(leaks))


@dataclass(frozen=True)
class WorkflowScope:
    source_label: str = DEFAULT_SOURCE_LABEL
    expected_current_media_count: int | None = None
    expected_eligible_count: int | None = None
    expected_ineligible_count: int | None = None
    strict: bool = False
    dry_run: bool = True
    null_content_class_policy: str = NULL_POLICY_HARD_FAIL
    report_title: str = "Phase 3.8b Classification-First E2E Dry-run"


@dataclass(frozen=True)
class StageContract:
    order: int
    name: str
    expected_input: str
    expected_output: str
    hard_gates: tuple[str, ...]
    expected_count_fields: tuple[str, ...]
    mutation_risk: str
    required_validation_artifacts: tuple[str, ...]
    implementation_status: str


@dataclass(frozen=True)
class MutationSnapshot:
    media: int
    media_tags: int
    ai_jobs: int
    classification_jobs: int
    translation_jobs: int


@dataclass(frozen=True)
class ScopeAudit:
    target_media_count: int
    content_class_distribution: dict[str, int]
    eligible_media_count: int
    ineligible_media_count: int
    null_content_class_count: int
    ai_associations: dict[str, int]
    legacy_contamination: dict[str, Any]
    localization_scope: dict[str, Any]
    tag_scope: dict[str, Any]
    active_jobs: dict[str, list[dict[str, Any]]]
    mutation_snapshot: MutationSnapshot


def workflow_stage_contracts() -> list[StageContract]:
    return [
        StageContract(
            1,
            "candidate manifest / candidate selection",
            "source candidate roots or approved candidate manifest",
            "privacy-safe candidate manifest with run_id/source_label",
            ("repo identity", "source read-only", "manifest privacy split", "expected candidate policy"),
            ("candidate_total", "candidate_selected", "candidate_excluded", "already_known"),
            "read-only source discovery; no source mutation allowed",
            ("candidate manifest", "selection summary"),
            "phase-runner only",
        ),
        StageContract(
            2,
            "staging copy",
            "candidate manifest",
            "staging directory plus copy manifest",
            ("source immutability", "staging containment", "disk space", "copy hash verification"),
            ("copied", "skipped", "failed", "bytes"),
            "file copy only in future execute; forbidden in Phase 3.8b",
            ("copy manifest", "copy summary"),
            "phase-runner only",
        ),
        StageContract(
            3,
            "pre-import audit",
            "staged files and copy manifest",
            "PASS/FAIL audit summary",
            ("expected count", "hash readable", "MIME/media probe safe", "privacy-safe report"),
            ("audit_pass", "audit_failed", "missing", "unsupported"),
            "read-only staged file inspection",
            ("pre-import audit summary",),
            "phase-runner only",
        ),
        StageContract(
            4,
            "DB import",
            "audited staged manifest",
            "Media rows and app-managed storage paths",
            ("Python identity", "DB identity", "storage root identity", "DB backup", "execute confirmation"),
            ("media_before", "media_after", "imported", "duplicates", "failed"),
            "DB/storage write in future execute; forbidden in Phase 3.8b",
            ("execute summary", "post-import audit"),
            "phase-runner only",
        ),
        StageContract(
            5,
            "content classification",
            "imported media set/source label",
            "content_class populated for target media",
            ("active job check", "classifier availability", "no AI/localization side effects"),
            ("processed", "anime", "unknown", "non_anime", "illustration", "failed"),
            "classification DB writes in future execute; forbidden in Phase 3.8b",
            ("classification summary",),
            "phase-runner only",
        ),
        StageContract(
            6,
            "eligible media selection: anime + unknown",
            "classified media set",
            "eligible media IDs and ineligible audit",
            ("all target rows classified", "NULL policy explicit", "expected eligible/ineligible counts"),
            ("eligible", "ineligible", "null_content_class", "legacy_ineligible_ai_associations"),
            "read-only scope query",
            ("scope audit",),
            "available",
        ),
        StageContract(
            7,
            "AI tagging only eligible media",
            "eligible media IDs",
            "AI tag associations/suggestions for eligible media only",
            ("active AI jobs none", "translation workers isolated", "eligible-only assertion"),
            ("processed", "failed", "confirmed_associations", "suggestions", "media_with_ai_tags_delta"),
            "AI DB writes in future execute; forbidden in Phase 3.8b",
            ("AI tagging summary", "AI failure report if needed"),
            "needs service extraction",
        ),
        StageContract(
            8,
            "localization only eligible-derived general/meta tags",
            "tags attached to eligible media",
            "TagTranslation rows for eligible-derived general/meta tags",
            ("eligible-media join", "proper nouns deferred", "LLM/provider gate", "worker isolation"),
            ("candidates", "translated", "failed", "skipped", "proper_noun_deferred", "remaining"),
            "translation DB writes in future execute; forbidden in Phase 3.8b",
            ("localization summary",),
            "needs service extraction",
        ),
        StageContract(
            9,
            "post-run validation",
            "DB/storage/reports",
            "read-only validation summary",
            ("no cleanup/delete", "count reconciliation", "endpoint sweep contract"),
            ("metadata_success", "detail_success", "thumbnail_success", "file_sample_success"),
            "read-only validation",
            ("validation summary",),
            "phase-runner only",
        ),
        StageContract(
            10,
            "browser/API smoke",
            "verified local server",
            "browser/API smoke result",
            ("server identity", "no stale server", "no destructive endpoints"),
            ("pages_checked", "flows_checked", "failures"),
            "read-only browser/API traffic",
            ("browser smoke summary", "server log scan"),
            "phase-runner only",
        ),
        StageContract(
            11,
            "report",
            "all stage artifacts",
            "privacy-safe final report",
            ("no secrets", "no local absolute paths", "exact counts", "failure artifact if needed"),
            ("reports_written", "privacy_leaks", "contract_failures"),
            "report file writes only",
            ("dry-run summary", "execute summary", "failure report if any"),
            "available",
        ),
    ]


def content_class_value(value: Any) -> str | None:
    return enum_value(value)


def is_eligible_content_class(value: Any, *, null_policy: str = NULL_POLICY_HARD_FAIL) -> bool:
    normalized = content_class_value(value)
    if normalized is None:
        return null_policy == NULL_POLICY_TREAT_AS_UNKNOWN
    return normalized in ELIGIBLE_CONTENT_CLASSES


def is_ineligible_content_class(value: Any, *, null_policy: str = NULL_POLICY_HARD_FAIL) -> bool:
    return not is_eligible_content_class(value, null_policy=null_policy)


def partition_content_class_counts(
    counts: Mapping[str | None, int],
    *,
    null_policy: str = NULL_POLICY_HARD_FAIL,
) -> dict[str, int]:
    eligible = 0
    ineligible = 0
    null_count = int(counts.get(None, 0) or 0) + int(counts.get("unclassified", 0) or 0)
    for raw, count in counts.items():
        normalized = raw if raw != "unclassified" else None
        if is_eligible_content_class(normalized, null_policy=null_policy):
            eligible += int(count)
        else:
            ineligible += int(count)
    return {"eligible": eligible, "ineligible": ineligible, "null_content_class": null_count}


def _content_class_distribution(db: Session, source_label: str) -> dict[str, int]:
    rows = (
        db.query(Media.content_class, func.count(Media.id))
        .filter(Media.source == source_label)
        .group_by(Media.content_class)
        .all()
    )
    result = {
        "anime": 0,
        "unknown": 0,
        "illustration": 0,
        "non_anime": 0,
        "unclassified": 0,
    }
    for content_class, count in rows:
        key = content_class_value(content_class) or "unclassified"
        result[key] = int(count)
    return result


def _eligible_condition(null_policy: str = NULL_POLICY_HARD_FAIL):
    base = Media.content_class.in_([ContentClassEnum.anime, ContentClassEnum.unknown])
    if null_policy == NULL_POLICY_TREAT_AS_UNKNOWN:
        return or_(Media.content_class.is_(None), base)
    return base


def _ineligible_condition(null_policy: str = NULL_POLICY_HARD_FAIL):
    base = Media.content_class.in_([ContentClassEnum.non_anime, ContentClassEnum.illustration])
    if null_policy == NULL_POLICY_TREAT_AS_UNKNOWN:
        return base
    return or_(Media.content_class.is_(None), base)


def select_eligible_media_ids(
    db: Session,
    source_label: str,
    *,
    limit: int | None = None,
    null_policy: str = NULL_POLICY_HARD_FAIL,
) -> list[int]:
    query = (
        db.query(Media.id)
        .filter(Media.source == source_label)
        .filter(_eligible_condition(null_policy))
        .order_by(Media.id.asc())
    )
    if limit is not None:
        query = query.limit(limit)
    return [int(row[0]) for row in query.all()]


def assert_ai_scope_media_ids_are_eligible(
    db: Session,
    media_ids: Sequence[int],
    *,
    source_label: str | None = None,
    null_policy: str = NULL_POLICY_HARD_FAIL,
) -> dict[str, Any]:
    if not media_ids:
        return {"checked": 0, "eligible_ids": [], "ineligible_ids": []}
    query = db.query(Media.id, Media.content_class, Media.source).filter(Media.id.in_(list(media_ids)))
    rows = query.all()
    found = {int(row.id): row for row in rows}
    missing = [int(mid) for mid in media_ids if int(mid) not in found]
    ineligible: list[dict[str, Any]] = []
    eligible_ids: list[int] = []
    for mid in media_ids:
        item = found.get(int(mid))
        if item is None:
            continue
        cls = content_class_value(item.content_class)
        wrong_source = source_label is not None and item.source != source_label
        if wrong_source or not is_eligible_content_class(cls, null_policy=null_policy):
            ineligible.append(
                {
                    "media_id": int(mid),
                    "content_class": cls or "unclassified",
                    "source_matches_scope": not wrong_source,
                }
            )
        else:
            eligible_ids.append(int(mid))
    if missing or ineligible:
        raise WorkflowContractError(
            "AI tagging scope contains non-eligible media: "
            + json.dumps({"missing_ids": missing, "ineligible_ids": ineligible}, ensure_ascii=False)
        )
    return {"checked": len(media_ids), "eligible_ids": eligible_ids, "ineligible_ids": []}


def collect_mutation_snapshot(db: Session) -> MutationSnapshot:
    return MutationSnapshot(
        media=int(db.query(func.count(Media.id)).scalar() or 0),
        media_tags=int(db.query(func.count()).select_from(blombooru_media_tags).scalar() or 0),
        ai_jobs=int(db.query(func.count(AITagJob.id)).scalar() or 0),
        classification_jobs=int(db.query(func.count(ClassificationJob.id)).scalar() or 0),
        translation_jobs=int(db.query(func.count(TagTranslationJob.id)).scalar() or 0),
    )


def compare_mutation_snapshots(before: MutationSnapshot, after: MutationSnapshot) -> dict[str, int]:
    before_dict = asdict(before)
    after_dict = asdict(after)
    return {key: int(after_dict[key]) - int(before_dict[key]) for key in before_dict}


def _count_target_ai_associations(db: Session, source_label: str, *, suggestions: bool | None = None) -> int:
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


def _count_media_with_ai_tags(
    db: Session,
    source_label: str,
    *,
    eligible: bool | None = None,
    null_policy: str = NULL_POLICY_HARD_FAIL,
) -> int:
    query = (
        db.query(func.count(func.distinct(blombooru_media_tags.c.media_id)))
        .select_from(blombooru_media_tags)
        .join(Media, Media.id == blombooru_media_tags.c.media_id)
        .filter(Media.source == source_label)
        .filter(blombooru_media_tags.c.source == AI_SOURCE)
    )
    if eligible is True:
        query = query.filter(_eligible_condition(null_policy))
    elif eligible is False:
        query = query.filter(_ineligible_condition(null_policy))
    return int(query.scalar() or 0)


def _count_distinct_ai_tags(
    db: Session,
    source_label: str,
    *,
    eligible: bool,
    null_policy: str = NULL_POLICY_HARD_FAIL,
) -> int:
    query = (
        db.query(func.count(func.distinct(blombooru_media_tags.c.tag_id)))
        .select_from(blombooru_media_tags)
        .join(Media, Media.id == blombooru_media_tags.c.media_id)
        .filter(Media.source == source_label)
        .filter(blombooru_media_tags.c.source == AI_SOURCE)
    )
    query = query.filter(_eligible_condition(null_policy) if eligible else _ineligible_condition(null_policy))
    return int(query.scalar() or 0)


def _count_ai_associations_by_scope(
    db: Session,
    source_label: str,
    *,
    eligible: bool,
    null_policy: str = NULL_POLICY_HARD_FAIL,
) -> int:
    query = (
        db.query(func.count())
        .select_from(blombooru_media_tags)
        .join(Media, Media.id == blombooru_media_tags.c.media_id)
        .filter(Media.source == source_label)
        .filter(blombooru_media_tags.c.source == AI_SOURCE)
    )
    query = query.filter(_eligible_condition(null_policy) if eligible else _ineligible_condition(null_policy))
    return int(query.scalar() or 0)


def _static_translation_names() -> set[str]:
    try:
        from .tag_localization_service import _load_static_dict

        data = _load_static_dict()
        return set(data.get("tags", {}).keys())
    except Exception:
        return set()


def select_eligible_localization_candidates(
    db: Session,
    source_label: str,
    *,
    lang: str = ZH_LANG,
    categories: Sequence[str] = LOCALIZABLE_CATEGORIES,
    limit: int | None = None,
    null_policy: str = NULL_POLICY_HARD_FAIL,
) -> list[dict[str, Any]]:
    category_enums = [TagCategoryEnum(category) for category in categories]
    translated = (
        db.query(TagTranslation.canonical_name)
        .filter(TagTranslation.language == lang)
        .filter(TagTranslation.status != "rejected")
        .subquery()
    )
    query = (
        db.query(Tag.id, Tag.name, Tag.category, func.count(func.distinct(Media.id)).label("eligible_media_count"))
        .select_from(Tag)
        .join(blombooru_media_tags, blombooru_media_tags.c.tag_id == Tag.id)
        .join(Media, Media.id == blombooru_media_tags.c.media_id)
        .filter(Media.source == source_label)
        .filter(_eligible_condition(null_policy))
        .filter(Tag.category.in_(category_enums))
        .filter(~Tag.name.in_(db.query(translated.c.canonical_name)))
        .group_by(Tag.id, Tag.name, Tag.category)
        .order_by(func.count(func.distinct(Media.id)).desc(), Tag.name.asc())
    )
    static_names = _static_translation_names()
    if static_names:
        query = query.filter(~Tag.name.in_(static_names))
    if limit is not None:
        query = query.limit(limit)
    return [
        {
            "tag_id": int(row.id),
            "canonical_name": str(row.name),
            "category": enum_value(row.category),
            "eligible_media_count": int(row.eligible_media_count),
        }
        for row in query.all()
    ]


def _count_missing_eligible_tags(
    db: Session,
    source_label: str,
    *,
    lang: str = ZH_LANG,
    categories: Sequence[str],
    null_policy: str = NULL_POLICY_HARD_FAIL,
) -> int:
    return len(
        select_eligible_localization_candidates(
            db,
            source_label,
            lang=lang,
            categories=categories,
            limit=None,
            null_policy=null_policy,
        )
    )


def _count_deferred_proper_noun_tags(
    db: Session,
    source_label: str,
    *,
    null_policy: str = NULL_POLICY_HARD_FAIL,
) -> int:
    return _count_missing_eligible_tags(
        db,
        source_label,
        categories=PROPER_NOUN_CATEGORIES,
        null_policy=null_policy,
    )


def _count_translated_tags_attached(
    db: Session,
    source_label: str,
    *,
    eligible: bool,
    null_policy: str = NULL_POLICY_HARD_FAIL,
) -> int:
    query = (
        db.query(func.count(func.distinct(TagTranslation.canonical_name)))
        .select_from(TagTranslation)
        .join(Tag, Tag.name == TagTranslation.canonical_name)
        .join(blombooru_media_tags, blombooru_media_tags.c.tag_id == Tag.id)
        .join(Media, Media.id == blombooru_media_tags.c.media_id)
        .filter(Media.source == source_label)
        .filter(TagTranslation.language == ZH_LANG)
        .filter(TagTranslation.status != "rejected")
    )
    query = query.filter(_eligible_condition(null_policy) if eligible else _ineligible_condition(null_policy))
    return int(query.scalar() or 0)


def _find_active_jobs(db: Session, model: Any, trigger_attr: str) -> list[dict[str, Any]]:
    rows = db.query(model).filter(model.status.in_(ACTIVE_JOB_STATUSES)).order_by(model.id.asc()).all()
    return [
        {
            "id": int(job.id),
            "status": str(job.status),
            "source": str(getattr(job, trigger_attr, "")),
        }
        for job in rows
    ]


def collect_scope_audit(db: Session, scope: WorkflowScope) -> ScopeAudit:
    distribution = _content_class_distribution(db, scope.source_label)
    target_media_count = int(sum(distribution.values()))
    partition = partition_content_class_counts(
        distribution,
        null_policy=scope.null_content_class_policy,
    )
    null_policy = scope.null_content_class_policy
    eligible_ai = _count_ai_associations_by_scope(db, scope.source_label, eligible=True, null_policy=null_policy)
    ineligible_ai = _count_ai_associations_by_scope(
        db,
        scope.source_label,
        eligible=False,
        null_policy=null_policy,
    )
    visual_candidates = _count_missing_eligible_tags(
        db,
        scope.source_label,
        categories=LOCALIZABLE_CATEGORIES,
        null_policy=null_policy,
    )
    proper_noun_deferred = _count_deferred_proper_noun_tags(
        db,
        scope.source_label,
        null_policy=null_policy,
    )
    mutation_snapshot = collect_mutation_snapshot(db)

    return ScopeAudit(
        target_media_count=target_media_count,
        content_class_distribution=distribution,
        eligible_media_count=partition["eligible"],
        ineligible_media_count=partition["ineligible"],
        null_content_class_count=partition["null_content_class"],
        ai_associations={
            "total": _count_target_ai_associations(db, scope.source_label),
            "confirmed": _count_target_ai_associations(db, scope.source_label, suggestions=False),
            "suggestions": _count_target_ai_associations(db, scope.source_label, suggestions=True),
            "eligible": eligible_ai,
            "ineligible": ineligible_ai,
            "media_with_ai_tags": _count_media_with_ai_tags(db, scope.source_label),
        },
        legacy_contamination={
            "status": "legacy_validation_artifact",
            "ineligible_media_with_ai_tags": _count_media_with_ai_tags(
                db,
                scope.source_label,
                eligible=False,
                null_policy=null_policy,
            ),
            "ineligible_ai_associations": ineligible_ai,
            "distinct_ai_tags_on_ineligible_media": _count_distinct_ai_tags(
                db,
                scope.source_label,
                eligible=False,
                null_policy=null_policy,
            ),
            "cleanup_performed": False,
            "policy": "report and filter from future tag-derived workflows; do not delete in Phase 3.8b",
        },
        localization_scope={
            "eligible_missing_general_meta_candidates": visual_candidates,
            "proper_noun_deferred_candidates": proper_noun_deferred,
            "categories_allowed_now": list(LOCALIZABLE_CATEGORIES),
            "categories_deferred": list(PROPER_NOUN_CATEGORIES),
            "translated_tag_names_attached_to_eligible_media": _count_translated_tags_attached(
                db,
                scope.source_label,
                eligible=True,
                null_policy=null_policy,
            ),
            "translated_tag_names_attached_to_ineligible_media": _count_translated_tags_attached(
                db,
                scope.source_label,
                eligible=False,
                null_policy=null_policy,
            ),
        },
        tag_scope={
            "distinct_ai_tags_on_eligible_media": _count_distinct_ai_tags(
                db,
                scope.source_label,
                eligible=True,
                null_policy=null_policy,
            ),
            "distinct_ai_tags_on_ineligible_media": _count_distinct_ai_tags(
                db,
                scope.source_label,
                eligible=False,
                null_policy=null_policy,
            ),
            "tag_stats_policy": "must use eligible media join; do not use global Tag.post_count for workflow stats",
            "similarity_policy": "must filter inputs through eligible media before tag-derived similarity",
        },
        active_jobs={
            "ai": _find_active_jobs(db, AITagJob, "trigger_source"),
            "classification": _find_active_jobs(db, ClassificationJob, "trigger_source"),
            "translation": _find_active_jobs(db, TagTranslationJob, "source"),
        },
        mutation_snapshot=mutation_snapshot,
    )


def redacted_database_url(settings: Any) -> str:
    return (
        f"postgresql://{getattr(settings, 'DB_USER', 'postgres')}:***@"
        f"{getattr(settings, 'DB_HOST', 'localhost')}:{getattr(settings, 'DB_PORT', 5432)}/"
        f"{getattr(settings, 'DB_NAME', '')}"
    )


def _run_git(repo_root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=str(repo_root), text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def build_identity_summary(*, repo_root: Path, settings: Any) -> dict[str, Any]:
    branch = _run_git(repo_root, "branch", "--show-current") or "<unknown>"
    head = _run_git(repo_root, "rev-parse", "HEAD") or "<unknown>"
    status = _run_git(repo_root, "status", "--porcelain")
    tracked_dirty = any(line and not line.startswith("??") for line in status.splitlines())
    return {
        "python": {
            "executable_label": Path(sys.executable).name,
            "version": sys.version.split()[0],
        },
        "repo": {
            "root_label": "repo_root",
            "branch": branch,
            "report_git_head_before_commit": head,
            "tracked_dirty": tracked_dirty,
            "report_generated_from_worktree": True,
        },
        "database": {
            "violet_env": getattr(settings, "VIOLET_ENV", ""),
            "db_name": getattr(settings, "DB_NAME", ""),
            "database_url_safe": redacted_database_url(settings),
        },
        "storage": {
            "storage_root_label": "app_storage",
            "storage_root_explicitly_set": bool(getattr(settings, "STORAGE_ROOT_EXPLICITLY_SET", False)),
            "paths_redacted": True,
        },
    }


def _expected_count_mismatches(scope: WorkflowScope, audit: ScopeAudit) -> list[str]:
    mismatches: list[str] = []
    if scope.expected_current_media_count is not None and audit.target_media_count != scope.expected_current_media_count:
        mismatches.append(
            f"expected_current_media_count={scope.expected_current_media_count}, found={audit.target_media_count}"
        )
    if scope.expected_eligible_count is not None and audit.eligible_media_count != scope.expected_eligible_count:
        mismatches.append(f"expected_eligible_count={scope.expected_eligible_count}, found={audit.eligible_media_count}")
    if scope.expected_ineligible_count is not None and audit.ineligible_media_count != scope.expected_ineligible_count:
        mismatches.append(
            f"expected_ineligible_count={scope.expected_ineligible_count}, found={audit.ineligible_media_count}"
        )
    return mismatches


def build_dry_run_report(
    db: Session,
    scope: WorkflowScope,
    *,
    repo_root: Path,
    settings: Any,
    started_at: str | None = None,
    before_snapshot: MutationSnapshot | None = None,
    after_snapshot: MutationSnapshot | None = None,
) -> dict[str, Any]:
    started = started_at or utc_now()
    before = before_snapshot or collect_mutation_snapshot(db)
    audit = collect_scope_audit(db, scope)
    after = after_snapshot or collect_mutation_snapshot(db)
    mutation_delta = compare_mutation_snapshots(before, after)
    contract_failures: list[str] = []
    warnings: list[str] = []
    expected_mismatches = _expected_count_mismatches(scope, audit)
    if expected_mismatches:
        if scope.strict:
            contract_failures.extend(expected_mismatches)
        else:
            warnings.extend(f"non-strict expected count mismatch: {item}" for item in expected_mismatches)
    if any(value != 0 for value in mutation_delta.values()):
        contract_failures.append(f"dry-run mutation detected: {mutation_delta}")

    if audit.null_content_class_count:
        warnings.append(
            "NULL content_class rows are reported in dry-run; formal execute must hard fail unless approved."
        )
    if any(audit.active_jobs.values()):
        warnings.append("Active background jobs are present; future execute would be blocked.")

    report: dict[str, Any] = {
        "phase": "3.8b",
        "mode": "dry_run",
        "success": not contract_failures,
        "status": "passed" if not contract_failures else "failed_contract",
        "started_at": started,
        "finished_at": utc_now(),
        "scope": asdict(scope),
        "identity": build_identity_summary(repo_root=repo_root, settings=settings),
        "workflow_order": [stage.name for stage in workflow_stage_contracts()],
        "stage_contracts": [asdict(stage) for stage in workflow_stage_contracts()],
        "counts": {
            "target_media_count": audit.target_media_count,
            "content_class_distribution": audit.content_class_distribution,
            "eligible_media_count": audit.eligible_media_count,
            "ineligible_media_count": audit.ineligible_media_count,
            "null_content_class_count": audit.null_content_class_count,
            "ai_associations": audit.ai_associations,
            "localization_scope": audit.localization_scope,
            "tag_scope": audit.tag_scope,
        },
        "legacy_contamination": audit.legacy_contamination,
        "active_jobs": audit.active_jobs,
        "mutation_safety": {
            "before": asdict(before),
            "after": asdict(after),
            "delta": mutation_delta,
            "passed": all(value == 0 for value in mutation_delta.values()),
        },
        "execute_policy": {
            "execute_supported_in_phase": False,
            "execute_rejection_message": (
                "Phase 3.8b supports dry-run planning only; execute is not implemented in this phase."
            ),
            "null_content_class_policy": scope.null_content_class_policy,
            "future_execute_requires_explicit_confirmation": True,
        },
        "validation_contract": {
            "endpoint_sweeps": [
                "metadata endpoint sweep",
                "media detail endpoint sweep",
                "thumbnail sweep",
                "file endpoint sample",
            ],
            "smoke_checks": [
                "content_class filters",
                "search/localization",
                "AI Review",
                "Admin status",
                "server log scan",
                "browser smoke",
            ],
        },
        "contract_failures": contract_failures,
        "warnings": warnings,
    }
    safe_report = sanitize_public_obj(report)
    leaks = find_privacy_leaks(safe_report)
    safe_report["privacy"] = {
        "paths_redacted": True,
        "secret_values_redacted": True,
        "leaks": leaks,
        "passed": not leaks,
    }
    if leaks:
        safe_report["success"] = False
        safe_report["status"] = "failed_privacy"
        safe_report["contract_failures"].append(f"privacy leaks detected: {leaks}")
    return safe_report


def write_json_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_report = sanitize_public_obj(dict(report))
    path.write_text(json.dumps(safe_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_markdown_report(report: Mapping[str, Any]) -> str:
    counts = report["counts"]
    identity = report["identity"]
    mutation = report["mutation_safety"]
    privacy = report["privacy"]
    lines = [
        "# Phase 3.8b Classification-First E2E Dry-run",
        "",
        "## Summary",
        "",
        f"- Mode: `{report['mode']}`",
        f"- Status: `{report['status']}`",
        f"- Success: `{report['success']}`",
        f"- Source label: `{report['scope']['source_label']}`",
        f"- Repo branch: `{identity['repo']['branch']}`",
        f"- Report git head before commit: `{identity['repo']['report_git_head_before_commit']}`",
        f"- Tracked dirty at report generation: `{identity['repo']['tracked_dirty']}`",
        f"- Python: `{identity['python']['executable_label']}` `{identity['python']['version']}`",
        f"- DB: `{identity['database']['violet_env']}` / `{identity['database']['db_name']}`",
        f"- Storage: `{identity['storage']['storage_root_label']}` (paths redacted)",
        "",
        "## Counts",
        "",
        f"- Target media: `{counts['target_media_count']}`",
        f"- Eligible media: `{counts['eligible_media_count']}`",
        f"- Ineligible media: `{counts['ineligible_media_count']}`",
        f"- NULL content_class: `{counts['null_content_class_count']}`",
        f"- Content class distribution: `{json.dumps(counts['content_class_distribution'], sort_keys=True)}`",
        f"- AI associations: `{json.dumps(counts['ai_associations'], sort_keys=True)}`",
        "",
        "## Legacy Contamination",
        "",
        f"- Status: `{report['legacy_contamination']['status']}`",
        f"- Ineligible media with AI tags: `{report['legacy_contamination']['ineligible_media_with_ai_tags']}`",
        f"- Ineligible AI associations: `{report['legacy_contamination']['ineligible_ai_associations']}`",
        f"- Cleanup performed: `{report['legacy_contamination']['cleanup_performed']}`",
        "",
        "## Localization Scope",
        "",
        f"- Eligible missing general/meta candidates: `{counts['localization_scope']['eligible_missing_general_meta_candidates']}`",
        f"- Proper-noun deferred candidates: `{counts['localization_scope']['proper_noun_deferred_candidates']}`",
        "",
        "## Stage Contracts",
        "",
        "| # | stage | status | mutation risk |",
        "|---:|---|---|---|",
    ]
    for stage in report["stage_contracts"]:
        lines.append(
            f"| {stage['order']} | {stage['name']} | {stage['implementation_status']} | {stage['mutation_risk']} |"
        )
    lines.extend(
        [
            "",
            "## Mutation Safety",
            "",
            f"- Before: `{json.dumps(mutation['before'], sort_keys=True)}`",
            f"- After: `{json.dumps(mutation['after'], sort_keys=True)}`",
            f"- Delta: `{json.dumps(mutation['delta'], sort_keys=True)}`",
            f"- Passed: `{mutation['passed']}`",
            "",
            "## Privacy",
            "",
            f"- Passed: `{privacy['passed']}`",
            f"- Leaks: `{json.dumps(privacy['leaks'])}`",
            "",
            "## Contract Failures",
            "",
        ]
    )
    failures = report.get("contract_failures", [])
    if failures:
        lines.extend(f"- {failure}" for failure in failures)
    else:
        lines.append("- None")
    lines.extend(["", "## Warnings", ""])
    warnings = report.get("warnings", [])
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Safety Confirmation",
            "",
            "- Dry-run only.",
            "- No import/copy/staging mutation.",
            "- No DB mutation.",
            "- No classification, AI tagging, localization, Entity Resolver, or similarity execution.",
            "- No cleanup/delete/reset/drop/truncate.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_markdown_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = sanitize_public_text(render_markdown_report(report))
    path.write_text(text, encoding="utf-8")
