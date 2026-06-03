"""Phase 4.4-P2R-F5 provider-neutral source name registry foundation.

Lifecycle: phase-scoped operational runner plus additive source-layer storage
foundation. It writes only the F5 source metadata/tag/name/alias/evidence
staging tables when `--apply-db` is explicitly used.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import migrate_add_source_metadata_name_registry  # noqa: E402
from app.models import Media  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.llm_translation_provider import (  # noqa: E402
    BaseLLMProvider,
    FallbackProvider,
    LLMAllProvidersFailed,
    LLMBatchAggregateError,
    LLMHTTPStatusError,
    LLMProviderError,
    LLMResponseFormatError,
    LLMTransportError,
    OpenAICompatibleProvider,
)
from app.services.source_metadata_registry_service import (  # noqa: E402
    CuratedNameMapping,
    SourceRegistryBundle,
    SourceSearchableNameAssertionDraft,
    build_name_search_index,
    build_source_registry_bundle,
    bundle_public_counts,
    canonical_source_key,
    normalize_source_text,
    persist_source_registry_bundle,
    provider_record_lookup_key,
    provider_name_coverage,
    raw_applicable_signal_rows,
    validate_search_queries,
)
from scripts import run_phase44p2r_f1_gallery_dl_json_import_pilot as f1  # noqa: E402
from scripts import run_phase44p2r_f2_gallery_dl_external_adapter_pilot as f2  # noqa: E402

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

PHASE = "4.4-P2R-F5"
PHASE_SLUG = "phase-4.4p2r-f5-provider-neutral-source-name-registry"
TITLE = "Provider-Neutral Source Metadata + Source Name / Alias Registry Foundation"
DATA_TYPE_REAL = "real_live_or_local_provider_data"
DATA_TYPE_ARTIFACT = "existing_artifact_or_report_derived"
DATA_TYPE_FIXTURE = "fixture_or_mock"
DATA_TYPE_LABELS = {DATA_TYPE_REAL, DATA_TYPE_ARTIFACT, DATA_TYPE_FIXTURE}
TARGET_RECORD_COUNT_DEFAULT = 200
MIN_RECORD_COUNT = 75
REAL_PIXIV_SOURCE_PRIOR_MIN = 50
REAL_PIXIV_METADATA_RICH_MIN = 60
REAL_PIXIV_APPLICABLE_NAME_SIGNAL_TARGET = 40
SAUCENAO_ARTIFACT_RECORD_TARGET = 20
REAL_PIXIV_GALLERY_DL_ATTEMPT_LIMIT_DEFAULT = 120
MAX_RECORD_COUNT = 500
SOURCE_ASSERTION_PROMPT_VERSION = "phase44p2r_f5_source_searchable_name_assertion_v1"
SOURCE_ASSERTION_SCHEMA_VERSION = "source_searchable_name_assertion_v1"
SOURCE_ASSERTION_DEFAULT_MAX_CANDIDATES = 300
SOURCE_ASSERTION_HARD_MAX_CANDIDATES = 500
ASSERTION_ROLES = frozenset(
    {
        "character",
        "person",
        "artist",
        "creator",
        "work_title",
        "source_title",
        "general_descriptor",
        "popularity_marker",
        "unknown",
    }
)
ASSERTION_STATUSES = frozenset({"searchable_active", "unresolved", "rejected", "needs_review"})
ASSERTION_CONFIDENCES = frozenset({"high", "medium", "low"})
ASSERTION_REASON_CODES = frozenset(
    {
        "parenthetical_character_work",
        "known_character_name",
        "known_work_title",
        "source_title_context",
        "known_artist_creator",
        "popularity_marker",
        "descriptive_tag",
        "generic_label",
        "ambiguous_without_context",
        "not_name_like",
        "insufficient_evidence",
        "model_output_invalid",
    }
)
PARENTHETICAL_CANDIDATE_RE = re.compile(r"^(.+?)[(（]([^()（）]+)[)）]$")

NON_SEARCHABLE_REJECT_REASON_CODES = frozenset(
    {
        "descriptive_tag",
        "generic_label",
        "not_name_like",
        "popularity_marker",
    }
)

REPORT_MD = Path(f"docs/reports/{PHASE_SLUG}.md")
REPORT_JSON = Path(f"docs/reports/{PHASE_SLUG}-summary.json")
PHASE_OUTPUT_DIR = Path(".local_manifests") / PHASE_SLUG
PRIVATE_DETAILS_JSON = PHASE_OUTPUT_DIR / "details.json"
PRIVATE_SOURCE_METADATA_CSV = PHASE_OUTPUT_DIR / "source-metadata-records.csv"
PRIVATE_SOURCE_TAG_OBSERVATIONS_CSV = PHASE_OUTPUT_DIR / "source-tag-observations.csv"
PRIVATE_SOURCE_TAG_REGISTRY_CSV = PHASE_OUTPUT_DIR / "source-tag-registry.csv"
PRIVATE_SOURCE_NAME_OBSERVATIONS_CSV = PHASE_OUTPUT_DIR / "source-name-observations.csv"
PRIVATE_SOURCE_NAME_REGISTRY_CSV = PHASE_OUTPUT_DIR / "source-name-registry.csv"
PRIVATE_SOURCE_NAME_ALIAS_CANDIDATES_CSV = PHASE_OUTPUT_DIR / "source-name-alias-candidates.csv"
PRIVATE_SOURCE_METADATA_EVIDENCE_CSV = PHASE_OUTPUT_DIR / "source-metadata-evidence.csv"
PRIVATE_SOURCE_SEARCHABLE_NAME_ASSERTIONS_CSV = PHASE_OUTPUT_DIR / "source-searchable-name-assertions.csv"
PRIVATE_MODEL_CLASSIFICATION_INPUTS_JSONL = PHASE_OUTPUT_DIR / "model-classification-inputs.jsonl"
PRIVATE_MODEL_CLASSIFICATION_OUTPUTS_JSONL = PHASE_OUTPUT_DIR / "model-classification-outputs.jsonl"
PRIVATE_MODEL_CLASSIFICATION_REVIEW_CSV = PHASE_OUTPUT_DIR / "model-classification-review.csv"
PRIVATE_REAL_PIXIV_SEARCHABLE_CANDIDATES_CSV = PHASE_OUTPUT_DIR / "real-pixiv-searchable-candidates.csv"
PRIVATE_REAL_PIXIV_CANDIDATE_INPUTS_CSV = PHASE_OUTPUT_DIR / "real-pixiv-candidate-inputs.csv"
PRIVATE_REAL_PIXIV_METADATA_RICH_RECORDS_CSV = PHASE_OUTPUT_DIR / "real-pixiv-metadata-rich-records.csv"
PRIVATE_GALLERY_DL_REAL_PIXIV_METADATA_DIR = PHASE_OUTPUT_DIR / "gallery-dl-real-pixiv-metadata"
PRIVATE_PROVIDER_NAME_COVERAGE_CSV = PHASE_OUTPUT_DIR / "provider-name-coverage.csv"
PRIVATE_RAW_APPLICABLE_NAME_SIGNALS_CSV = PHASE_OUTPUT_DIR / "raw-applicable-name-signals.csv"
PRIVATE_SEARCH_VALIDATION_CSV = PHASE_OUTPUT_DIR / "name-search-index-validation.csv"
PRIVATE_CURATED_TEMPLATE_CSV = PHASE_OUTPUT_DIR / "curated-name-mapping-template.csv"
PRIVATE_MANUAL_REVIEW_GUIDE = PHASE_OUTPUT_DIR / "manual-review-guide.md"

PR91_MERGE_COMMIT = "f6f02891aac5d7453e8dc3543209e6ff67c61815"
PR91_URL = "https://github.com/kyloris0660/VIOLET/pull/91"

ALLOWED_WRITE_TABLES = {
    "blombooru_source_metadata_records",
    "blombooru_source_tag_observations",
    "blombooru_source_tag_registry",
    "blombooru_source_name_observations",
    "blombooru_source_name_registry",
    "blombooru_source_name_alias_candidates",
    "blombooru_source_metadata_evidence",
    "blombooru_source_searchable_name_assertions",
}
FORBIDDEN_TABLES = {
    "blombooru_entities",
    "blombooru_entity_aliases",
    "blombooru_entity_external_identities",
    "blombooru_entity_evidence",
    "blombooru_media_entity_candidates",
    "blombooru_media_entity_assignments",
    "blombooru_provider_cache",
    "blombooru_negative_lookup_cache",
    "blombooru_media_tags",
    "blombooru_tag_translations",
    "blombooru_tag_translation_jobs",
}
F5_CLEANUP_TABLE_DELETE_ORDER = (
    "blombooru_source_searchable_name_assertions",
    "blombooru_source_metadata_evidence",
    "blombooru_source_name_alias_candidates",
    "blombooru_source_name_observations",
    "blombooru_source_tag_observations",
    "blombooru_source_name_registry",
    "blombooru_source_tag_registry",
    "blombooru_source_metadata_records",
)


class Phase44P2RF5Error(RuntimeError):
    pass


class OutputPathError(Phase44P2RF5Error):
    pass


class CuratedMappingError(Phase44P2RF5Error):
    pass


@dataclass(frozen=True)
class SearchableNameCandidate:
    candidate_key: str
    provider: str
    provider_record_key: str
    raw_input: str
    normalized_input: str
    data_type_label: str
    source_kind: str
    source_field: str | None = None
    role_hint: str | None = None
    source_tag_observation_key: str | None = None
    source_name_observation_key: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    parenthetical_outer: str | None = None
    parenthetical_inner: str | None = None
    high_impact: bool = False
    occurrence_count: int = 1


@dataclass(frozen=True)
class ValidatedModelAssertion:
    input: str
    normalized_input: str
    is_name_like: bool
    asserted_role: str
    extracted_name: str | None
    base_name: str | None
    work_context: str | None
    alias_candidates: tuple[str, ...]
    is_searchable_identity: bool
    searchable_status: str
    confidence: str
    reason_code: str
    evidence_summary: str
    requires_review: bool
    should_not_be_entity_truth: bool


def _rel(path: Path) -> str:
    return f1._rel(path)


def resolve_repo_path(path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else (ROOT / value).resolve()


def require_under_phase_output(path: Path) -> None:
    try:
        f1.require_under_path(resolve_repo_path(path), ROOT / PHASE_OUTPUT_DIR, code="f5_output_path_violation")
    except f1.OutputPathError as exc:
        raise OutputPathError(str(exc)) from exc


def generate_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{PHASE_SLUG}-{timestamp}-{uuid4().hex[:8]}"


def cleanup_phase_output_dir(output_dir: Path) -> dict[str, Any]:
    output_dir = resolve_repo_path(output_dir)
    require_under_phase_output(output_dir)
    summary = {
        "relative_artifact_dir": _rel(output_dir),
        "performed": False,
        "files_removed": 0,
        "directories_removed": 0,
        "bytes_removed": 0,
    }
    if not output_dir.exists():
        return summary
    for child in output_dir.iterdir():
        if child.is_dir():
            file_count = 0
            byte_count = 0
            for nested in child.rglob("*"):
                if nested.is_file():
                    file_count += 1
                    byte_count += nested.stat().st_size
            shutil.rmtree(child)
            summary["directories_removed"] += 1
            summary["files_removed"] += file_count
            summary["bytes_removed"] += byte_count
        elif child.is_file():
            summary["files_removed"] += 1
            summary["bytes_removed"] += child.stat().st_size
            child.unlink()
    summary["performed"] = True
    return summary


def attach_final_run_metadata(
    records: Sequence[Mapping[str, Any]],
    *,
    run_id: str,
    run_label: str,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for record in records:
        item = dict(record)
        item["provider_run_id"] = run_id
        item["run_label"] = run_label
        result.append(item)
    return result


def validate_public_report_paths_before_effects(*, report_json: Path, report_md: Path) -> None:
    for path in (report_json, report_md):
        try:
            f1.require_under_path(resolve_repo_path(path), ROOT / "docs/reports", code="f5_report_path_violation")
        except f1.OutputPathError as exc:
            raise OutputPathError(str(exc)) from exc


def validate_private_output_paths_before_effects(output_dir: Path, *, private_paths: Sequence[Path]) -> None:
    root = resolve_repo_path(output_dir)
    try:
        f1.require_under_path(root, ROOT / ".local_manifests", code="f5_output_path_violation")
    except f1.OutputPathError as exc:
        raise OutputPathError(str(exc)) from exc
    if PHASE_SLUG not in root.as_posix():
        raise OutputPathError("f5_output_path_violation")
    for path in private_paths:
        require_under_phase_output(path)


def validate_all_output_paths_before_effects(
    output_dir: Path,
    *,
    private_paths: Sequence[Path],
    report_json: Path,
    report_md: Path,
) -> None:
    validate_public_report_paths_before_effects(report_json=report_json, report_md=report_md)
    validate_private_output_paths_before_effects(output_dir, private_paths=private_paths)


def write_public_json(path: Path, payload: Any) -> None:
    f1.write_json(resolve_repo_path(path), payload, expected_parent=Path("docs/reports"))


def write_public_text(path: Path, content: str, *, private_markers: Iterable[str]) -> None:
    f1.assert_public_payload_safe(content, private_markers=private_markers)
    f1.write_text(resolve_repo_path(path), content, expected_parent=Path("docs/reports"))


def write_private_text(path: Path, content: str) -> None:
    resolved = resolve_repo_path(path)
    require_under_phase_output(resolved)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8", newline="\n")


def write_private_json(path: Path, payload: Any) -> None:
    write_private_text(
        path,
        json.dumps(f1._coerce_json_safe(payload), ensure_ascii=False, indent=2, sort_keys=True, default=str),
    )


def load_provider_records(path: str | Path | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not path:
        return _ensure_data_type_labels(default_provider_shape_records(), default_label=DATA_TYPE_FIXTURE), {
            "input_source": "built_in_public_safe_provider_shape_fixtures",
            "real_pixiv_raw_metadata_available": False,
            "external_provider_requests": 0,
            "provider_uploads": 0,
        }
    resolved = resolve_repo_path(path)
    try:
        f1.require_under_path(resolved, ROOT, code="f5_input_path_violation")
    except f1.OutputPathError as exc:
        raise OutputPathError(str(exc)) from exc
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if isinstance(payload, Mapping):
        records = payload.get("records")
    else:
        records = payload
    if not isinstance(records, list) or not all(isinstance(row, Mapping) for row in records):
        raise Phase44P2RF5Error("provider_input_json_shape_invalid")
    return _ensure_data_type_labels([dict(row) for row in records], default_label=DATA_TYPE_FIXTURE), {
        "input_source": "operator_supplied_provider_records_json",
        "input_record_count": len(records),
        "real_pixiv_raw_metadata_available": True,
        "external_provider_requests": 0,
        "provider_uploads": 0,
    }


def _ensure_data_type_labels(records: Sequence[Mapping[str, Any]], *, default_label: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in records:
        item = dict(row)
        label = normalize_source_text(item.get("data_type_label") or item.get("source_data_type_label") or default_label)
        item["data_type_label"] = label if label in DATA_TYPE_LABELS else default_label
        result.append(item)
    return result


def load_curated_mappings(path: str | Path | None) -> list[CuratedNameMapping]:
    if not path:
        return []
    resolved = resolve_repo_path(path)
    try:
        f1.require_under_path(resolved, ROOT, code="curated_mapping_path_violation")
    except f1.OutputPathError as exc:
        raise CuratedMappingError(str(exc)) from exc
    if not resolved.exists():
        raise CuratedMappingError("curated_mapping_input_missing")
    if resolved.suffix.lower() == ".json":
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        rows = payload.get("mappings") if isinstance(payload, Mapping) else payload
        if not isinstance(rows, list):
            raise CuratedMappingError("curated_mapping_json_shape_invalid")
    else:
        with resolved.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    mappings: list[CuratedNameMapping] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        source_name = normalize_source_text(row.get("source_name"))
        target_name = normalize_source_text(row.get("target_name"))
        if not source_name or not target_name:
            continue
        mappings.append(
            CuratedNameMapping(
                source_name=source_name,
                target_name=target_name,
                relation_type=normalize_source_text(row.get("relation_type")) or "curated_alias",
                name_role=normalize_source_text(row.get("name_role")) or "unknown_name",
                candidate_namespace=normalize_source_text(row.get("candidate_namespace")) or "source_name",
                confidence=float(row.get("confidence") or 0.95),
                source=normalize_source_text(row.get("source")) or "curated_mapping",
                notes=normalize_source_text(row.get("notes")) or None,
            )
        )
    return mappings


def default_provider_shape_records() -> list[dict[str, Any]]:
    return [
        {
            "provider": "pixiv",
            "provider_record_key": "fixture:pixiv:alpha:p0",
            "source_work_id": "fixture_pixiv_alpha",
            "source_page_index": 0,
            "artist_name": "Creator Alpha",
            "artist_id": "creator-alpha",
            "title": "Work Alpha visual",
            "tags": ["Character Alpha (Work Alpha)", "blue hair", "1000users入り"],
            "metadata_kind": "gallery_dl_pixiv_metadata",
        },
        {
            "provider": "pixiv",
            "provider_record_key": "fixture:pixiv:beta:p0",
            "source_work_id": "fixture_pixiv_beta",
            "source_page_index": 0,
            "artist_name": "Creator Beta",
            "artist_id": "creator-beta",
            "title": "Work Beta visual",
            "tags": ["Character Beta(Work Beta)", "standing pose"],
            "metadata_kind": "gallery_dl_pixiv_metadata",
        },
        {
            "provider": "pixiv",
            "provider_record_key": "fixture:pixiv:gamma:p0",
            "source_work_id": "fixture_pixiv_gamma",
            "source_page_index": 0,
            "artist_name": "Creator Gamma",
            "artist_id": "creator-gamma",
            "title": "Original illustration",
            "tags": ["original", "landscape"],
            "metadata_kind": "gallery_dl_pixiv_metadata",
        },
        {
            "provider": "pixiv",
            "provider_record_key": "fixture:pixiv:delta:p0",
            "source_work_id": "fixture_pixiv_delta",
            "source_page_index": 0,
            "artist_name": "Creator Delta",
            "artist_id": "creator-delta",
            "title": "Work Delta visual",
            "tags": [
                {"name": "Character Delta", "category": "character"},
                {"name": "Work Delta", "category": "copyright"},
            ],
            "provider_canonical_aliases": {"Character Delta": ["Char Delta"]},
            "metadata_kind": "gallery_dl_pixiv_metadata",
        },
        {
            "provider": "pixiv",
            "provider_record_key": "fixture:pixiv:no-person-signal:p0",
            "source_work_id": "fixture_pixiv_no_person_signal",
            "source_page_index": 0,
            "tags": ["background", "color study"],
            "metadata_kind": "gallery_dl_pixiv_metadata",
        },
        {
            "provider": "saucenao",
            "provider_record_key": "fixture:saucenao:danbooru-alpha",
            "source_work_id": "fixture_saucenao_alpha",
            "similarity": 96.2,
            "artist": "Creator Alpha",
            "title": "Work Alpha",
            "characters": ["Character Alpha (Work Alpha)"],
            "tags": [{"name": "source exact match", "kind": "provider_label"}],
            "metadata_kind": "saucenao_style_metadata",
        },
        {
            "provider": "saucenao",
            "provider_record_key": "fixture:saucenao:creator-beta",
            "source_work_id": "fixture_saucenao_beta",
            "similarity": 91.9,
            "creator": "Creator Beta",
            "material": "Work Beta",
            "metadata_kind": "saucenao_style_metadata",
        },
        {
            "provider": "danbooru",
            "provider_record_key": "fixture:danbooru:tag-category",
            "source_work_id": "fixture_danbooru_post",
            "tags": [
                {"name": "Character Epsilon", "category": "character"},
                {"name": "Creator Epsilon", "category": "artist"},
                {"name": "Work Epsilon", "category": "copyright"},
                {"name": "smile", "category": "general"},
            ],
            "metadata_kind": "danbooru_style_tags",
        },
        {
            "provider": "google_vision",
            "provider_record_key": "fixture:google-vision:labels-only",
            "labels": [
                {"name": "anime", "kind": "generic_provider_label"},
                {"name": "illustration", "kind": "generic_provider_label"},
            ],
            "metadata_kind": "generic_label_provider",
        },
        {
            "provider": "no_tag_provider",
            "provider_record_key": "fixture:no-tag:no-name",
            "metadata_kind": "no_tag_no_name_provider",
        },
    ]


PIXIV_ARTWORK_RE = re.compile(r"pixiv\.net/(?:en/)?artworks/([1-9]\d{5,12})", re.IGNORECASE)
PIXIV_FILE_RE = re.compile(r"(?<!\d)([1-9]\d{5,12})_p(\d{1,4})(?!\d)")
CANONICAL_LOCAL_ROOT = Path.home() / "Documents" / "AnimeLocalBooru"
CANONICAL_LOCAL_MANIFESTS = CANONICAL_LOCAL_ROOT / ".local_manifests"


def is_real_pixiv_metadata_rich_record(row: Mapping[str, Any]) -> bool:
    provider = canonical_source_key(row.get("provider") or "")
    data_type = normalize_source_text(row.get("data_type_label"))
    if provider != "pixiv" or data_type != DATA_TYPE_REAL:
        return False
    tags = _split_tag_text(row.get("tags"))
    raw_metadata = row.get("raw_metadata_json") if isinstance(row.get("raw_metadata_json"), Mapping) else {}
    provenance = row.get("provenance") if isinstance(row.get("provenance"), Mapping) else {}
    return bool(
        tags
        or normalize_source_text(row.get("title"))
        or normalize_source_text(row.get("artist_name") or row.get("artist"))
        or row.get("page_count") is not None
        or raw_metadata.get("metadata_richness")
        or provenance.get("raw_provider_payload_available")
        or provenance.get("gallery_dl_metadata_only")
    )


def pixiv_gallery_dl_record_to_source_record(
    record: f1.PixivGalleryDlMetadataRecord,
    *,
    source_prior_lookup: Mapping[tuple[str, int], Mapping[str, Any]],
    source_index: int,
    stdout_path: Path,
) -> dict[str, Any] | None:
    work_id = normalize_source_text(record.work_id)
    if not work_id:
        return None
    page_index = int(record.page_index or 0)
    prior = source_prior_lookup.get((work_id, page_index))
    if prior is None:
        return None
    tags = [tag for tag in [*record.tags, *record.translated_tags] if isinstance(tag, str) and normalize_source_text(tag)]
    row = {
        "provider": "pixiv",
        "provider_record_key": f"gallery-dl-real-pixiv:metadata:{source_index}:p{page_index}",
        "media_id": _safe_int(prior.get("media_id"), default=0) or None,
        "source_work_id": work_id,
        "source_page_index": page_index,
        "source_url": record.canonical_url,
        "title": record.title,
        "artist_name": record.artist_name,
        "artist_id": record.artist_id,
        "tags": tags,
        "page_count": record.page_count,
        "metadata_kind": "gallery_dl_real_pixiv_metadata",
        "data_type_label": DATA_TYPE_REAL,
        "_disable_name_extraction_fields": ["title", "pixiv_title"],
        "_source_title_only_fields": ["title", "pixiv_title"],
        "raw_metadata_json": {
            "source_prior_kind": "pixiv_work_id_page_index",
            "metadata_acquisition_route": "bounded_gallery_dl_dump_json_no_download",
            "metadata_richness": record.metadata_richness,
            "record_shape": record.record_shape,
            "page_count_present": record.page_count is not None,
            "title_present": bool(record.title),
            "artist_name_present": bool(record.artist_name),
            "artist_id_present": record.artist_id is not None,
            "tag_count": len(tags),
            "canonical_url_present": bool(record.canonical_url),
            "raw_provider_payload_available_private_artifact": True,
            "private_stdout_artifact": _rel(resolve_repo_path(stdout_path)),
            "exact_source_prior_match": True,
            "exact_source_prior_page_index": page_index,
            "exact_pixiv_id_publicly_redacted": True,
            "local_path_included": False,
            "filename_included": False,
        },
        "provenance": {
            "source": "bounded_gallery_dl_metadata_regeneration",
            "read_only": True,
            "external_provider_request_in_this_run": True,
            "gallery_dl_metadata_only": True,
            "exact_source_prior_match": True,
            "raw_provider_payload_available": True,
            "no_download": True,
            "image_upload": False,
            "local_path_included": False,
            "filename_included": False,
        },
    }
    return row if is_real_pixiv_metadata_rich_record(row) else None


def enrich_real_pixiv_source_priors_with_gallery_dl(
    source_prior_records: Sequence[Mapping[str, Any]],
    *,
    target_count: int,
    attempt_limit: int,
    raw_output_dir: Path,
    gallery_dl_command: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_output_dir = resolve_repo_path(raw_output_dir)
    require_under_phase_output(raw_output_dir)
    summary: dict[str, Any] = {
        "attempted": False,
        "metadata_acquisition_route": "bounded_gallery_dl_dump_json_no_download",
        "source_prior_input_count": len(source_prior_records),
        "source_prior_only_records_count": len(source_prior_records),
        "metadata_rich_target": int(target_count),
        "gallery_dl_attempt_limit": int(attempt_limit),
        "gallery_dl_attempted_count": 0,
        "gallery_dl_success_count": 0,
        "gallery_dl_failure_count": 0,
        "cached_private_artifact_hit_count": 0,
        "metadata_rich_record_count": 0,
        "field_presence_counts": {},
        "command_status_counts": {},
        "gallery_dl_available": False,
        "gallery_dl_mode": None,
        "gallery_dl_version_present": False,
        "external_provider_requests": 0,
        "image_downloads": 0,
        "image_uploads": 0,
        "raw_payload_publicly_redacted": True,
    }
    if target_count <= 0 or attempt_limit <= 0 or not source_prior_records:
        return [], summary

    try:
        entrypoint = f2.probe_gallery_dl_entrypoint(
            gallery_dl_command or None,
            python_executable=sys.executable,
        )
    except f2.GalleryDlUnavailable as exc:
        summary.update(
            {
                "attempted": True,
                "gallery_dl_available": False,
                "failure_reason": normalize_source_text(str(exc)) or type(exc).__name__,
            }
        )
        return [], summary

    summary.update(
        {
            "attempted": True,
            "gallery_dl_available": True,
            "gallery_dl_mode": entrypoint.mode,
            "gallery_dl_version_present": bool(entrypoint.version),
            "command_entrypoint_public_label": f2._public_command_label(entrypoint),
        }
    )
    raw_output_dir.mkdir(parents=True, exist_ok=True)
    source_prior_lookup: dict[tuple[str, int], Mapping[str, Any]] = {}
    selected_work_ids: list[str] = []
    seen_work_ids: set[str] = set()
    for row in source_prior_records:
        work_id = normalize_source_text(row.get("source_work_id") or row.get("work_id"))
        if not work_id:
            continue
        page = _safe_int(row.get("source_page_index", row.get("page_index")), default=0)
        source_prior_lookup.setdefault((work_id, page), row)
        if work_id not in seen_work_ids and len(selected_work_ids) < attempt_limit:
            selected_work_ids.append(work_id)
            seen_work_ids.add(work_id)

    rows: list[dict[str, Any]] = []
    seen_records: set[tuple[str, int]] = set()
    status_counts: Counter[str] = Counter()
    field_counts: Counter[str] = Counter()

    for item_index, work_id in enumerate(selected_work_ids, start=1):
        if len(rows) >= target_count:
            break
        stdout_path = raw_output_dir / f"metadata-{item_index:03d}.jsonl"
        stderr_path = raw_output_dir / f"metadata-{item_index:03d}.stderr.txt"
        if stdout_path.exists() and stdout_path.stat().st_size > 0:
            summary["cached_private_artifact_hit_count"] += 1
        else:
            command = f2.build_metadata_command(entrypoint, work_id)
            summary["gallery_dl_attempted_count"] += 1
            summary["external_provider_requests"] += 1
            try:
                completed = subprocess.run(
                    command,
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=90,
                    shell=False,
                    check=False,
                )
            except subprocess.SubprocessError as exc:
                status_counts[type(exc).__name__] += 1
                summary["gallery_dl_failure_count"] += 1
                continue
            stdout_path.write_text(completed.stdout or "", encoding="utf-8", newline="\n")
            stderr_path.write_text(
                f2.redact_text(completed.stderr or "", private_markers=[work_id]),
                encoding="utf-8",
                newline="\n",
            )
            if completed.returncode != 0:
                status_counts[f"exit_{completed.returncode}"] += 1
                summary["gallery_dl_failure_count"] += 1
                continue
        try:
            parse_result = f1.parse_gallery_dl_json_inputs(stdout_path, skip_invalid=True)
            normalized_records = f1.normalize_records(parse_result, adapter_version=entrypoint.version)
        except Exception as exc:
            status_counts[f"parse_{type(exc).__name__}"] += 1
            summary["gallery_dl_failure_count"] += 1
            continue
        successful_for_item = False
        for normalized in normalized_records:
            source_row = pixiv_gallery_dl_record_to_source_record(
                normalized,
                source_prior_lookup=source_prior_lookup,
                source_index=item_index,
                stdout_path=stdout_path,
            )
            if source_row is None:
                continue
            key = (
                normalize_source_text(source_row.get("source_work_id")),
                _safe_int(source_row.get("source_page_index"), default=0),
            )
            if key in seen_records:
                continue
            seen_records.add(key)
            rows.append(source_row)
            successful_for_item = True
            if source_row.get("title"):
                field_counts["title"] += 1
            if source_row.get("artist_name"):
                field_counts["artist_name"] += 1
            if _split_tag_text(source_row.get("tags")):
                field_counts["tags"] += 1
            if source_row.get("page_count") is not None:
                field_counts["page_count"] += 1
            if source_row.get("source_url"):
                field_counts["source_url"] += 1
            if len(rows) >= target_count:
                break
        if successful_for_item:
            status_counts["success"] += 1
            summary["gallery_dl_success_count"] += 1
        else:
            status_counts["no_metadata_rich_media_record"] += 1
            summary["gallery_dl_failure_count"] += 1

    summary["metadata_rich_record_count"] = len(rows)
    summary["source_prior_only_records_count"] = max(0, len(source_prior_records) - len(rows))
    summary["field_presence_counts"] = dict(sorted(field_counts.items()))
    summary["command_status_counts"] = dict(sorted(status_counts.items()))
    summary["metadata_rich_minimum_met"] = len(rows) >= REAL_PIXIV_METADATA_RICH_MIN
    return rows, summary


def scale_provider_records(
    records: Sequence[Mapping[str, Any]],
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    target_count = max(MIN_RECORD_COUNT, min(int(args.target_record_count), int(args.max_records), MAX_RECORD_COUNT))
    if args.input_json or args.disable_scale_up:
        return list(records), {
            "scale_up_enabled": False,
            "scale_up_reason": "operator_input_or_disabled",
            "target_record_count": target_count,
        }

    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    added_counts: Counter[str] = Counter()

    def add_many(rows: Iterable[Mapping[str, Any]], *, source_bucket: str) -> int:
        added = 0
        for row in rows:
            if len(result) >= target_count:
                break
            item = dict(row)
            provider = canonical_source_key(item.get("provider") or "generic_provider")
            provider_record_key = normalize_source_text(item.get("provider_record_key"))
            key = (provider, provider_record_key)
            if not provider or not provider_record_key or key in seen:
                continue
            result.append(item)
            seen.add(key)
            added += 1
            added_counts[source_bucket] += 1
        return added

    real_pixiv_source_prior_records: list[dict[str, Any]] = []
    real_pixiv_summary: dict[str, Any] = {"attempted": False, "record_count": 0}
    real_pixiv_enriched_records: list[dict[str, Any]] = []
    real_pixiv_enrichment_summary: dict[str, Any] = {"attempted": False, "metadata_rich_record_count": 0}
    if not args.no_db and not args.dry_run:
        source_prior_limit = max(
            int(args.real_pixiv_source_prior_min),
            int(args.real_pixiv_metadata_rich_min),
            min(int(args.gallery_dl_metadata_attempt_limit), MAX_RECORD_COUNT),
            REAL_PIXIV_SOURCE_PRIOR_MIN,
        )
        real_pixiv_source_prior_records, real_pixiv_summary = load_real_pixiv_source_prior_records_from_db(
            limit=source_prior_limit
        )
        if not args.disable_gallery_dl_metadata_enrichment:
            real_pixiv_enriched_records, real_pixiv_enrichment_summary = enrich_real_pixiv_source_priors_with_gallery_dl(
                real_pixiv_source_prior_records,
                target_count=int(args.real_pixiv_metadata_rich_min),
                attempt_limit=int(args.gallery_dl_metadata_attempt_limit),
                raw_output_dir=resolve_repo_path(args.gallery_dl_metadata_raw_dir),
                gallery_dl_command=args.gallery_dl_command or None,
            )
    add_many(real_pixiv_enriched_records, source_bucket="real_pixiv_gallery_dl_metadata_rich")

    pixiv_artifact_records, pixiv_artifact_summary = load_existing_pixiv_artifact_records(
        limit=max(REAL_PIXIV_APPLICABLE_NAME_SIGNAL_TARGET, 20)
    )
    add_many(pixiv_artifact_records, source_bucket="pixiv_existing_artifact_or_gallery_dl")
    artifact_saucenao_records = load_existing_saucenao_artifact_records(limit=SAUCENAO_ARTIFACT_RECORD_TARGET)
    add_many(artifact_saucenao_records, source_bucket="saucenao_existing_artifact_or_report")
    add_many(records, source_bucket="built_in_fixture_records")
    add_many(generated_scale_fixture_records(start_index=1, count=target_count - len(result)), source_bucket="generated_fixture_records")

    final_records = result[: min(int(args.max_records), MAX_RECORD_COUNT)]
    data_type_counts = Counter(row.get("data_type_label") for row in final_records)
    provider_data_type_counts = Counter(
        f"{canonical_source_key(row.get('provider') or 'generic_provider')}:{row.get('data_type_label')}"
        for row in final_records
    )
    return final_records, {
        "scale_up_enabled": True,
        "target_record_count": target_count,
        "actual_record_count": len(final_records),
        "minimum_record_count_required": MIN_RECORD_COUNT,
        "preferred_record_count": TARGET_RECORD_COUNT_DEFAULT,
        "hard_max_record_count": MAX_RECORD_COUNT,
        "requested_max_record_count": int(args.max_records),
        "data_type_counts": dict(sorted(data_type_counts.items())),
        "provider_data_type_counts": dict(sorted(provider_data_type_counts.items())),
        "added_source_bucket_counts": dict(sorted(added_counts.items())),
        "real_pixiv_source_prior_minimum": int(args.real_pixiv_source_prior_min),
        "real_pixiv_source_prior_summary": real_pixiv_summary,
        "real_pixiv_metadata_rich_minimum": int(args.real_pixiv_metadata_rich_min),
        "real_pixiv_metadata_enrichment_summary": real_pixiv_enrichment_summary,
        "existing_pixiv_artifact_summary": pixiv_artifact_summary,
        "existing_saucenao_artifact_record_count": added_counts.get("saucenao_existing_artifact_or_report", 0),
        "existing_saucenao_artifact_target": SAUCENAO_ARTIFACT_RECORD_TARGET,
        "fixture_or_mock_record_count": data_type_counts.get(DATA_TYPE_FIXTURE, 0),
        "meets_scale_minimum": len(final_records) >= MIN_RECORD_COUNT,
        "meets_real_pixiv_source_prior_minimum": real_pixiv_summary.get("record_count", 0)
        >= int(args.real_pixiv_source_prior_min),
        "meets_real_pixiv_metadata_rich_minimum": (
            real_pixiv_enrichment_summary.get("metadata_rich_record_count", 0)
            >= int(args.real_pixiv_metadata_rich_min)
        ),
    }


def load_real_pixiv_source_prior_records_from_db(*, limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    config = load_f5_project_config()
    engine = create_engine(config.database_url)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    records: list[dict[str, Any]] = []
    scanned = 0
    try:
        db_identity = f1.prove_db_identity(session, config)
        for media_id, filename, source in session.query(Media.id, Media.filename, Media.source).order_by(Media.id.asc()):
            scanned += 1
            prior = _pixiv_source_prior_from_values(filename=filename, source=source)
            if prior is None:
                continue
            work_id, page_index, source_url, matched_field = prior
            records.append(
                {
                    "provider": "pixiv",
                    "provider_record_key": f"db-source-prior:pixiv:{work_id}:p{page_index}:m{media_id}",
                    "media_id": int(media_id),
                    "source_work_id": work_id,
                    "source_page_index": page_index,
                    "source_url": source_url,
                    "metadata_kind": "local_pixiv_source_prior",
                    "data_type_label": DATA_TYPE_REAL,
                    "raw_metadata_json": {
                        "source_prior_kind": "pixiv_work_id_page_index",
                        "work_id": work_id,
                        "page_index": page_index,
                        "matched_field": matched_field,
                        "local_path_included": False,
                        "filename_included": False,
                    },
                    "provenance": {
                        "source": "local_db_media_source_or_filename_prior",
                        "read_only": True,
                        "external_provider_request": False,
                    },
                }
            )
            if len(records) >= limit:
                break
        return records, {
            "attempted": True,
            "record_count": len(records),
            "scanned_media_count": scanned,
            "db_identity": {
                key: db_identity.get(key)
                for key in (
                    "violet_env",
                    "actual_db_name",
                    "configured_db_name",
                    "configured_db_host",
                    "configured_db_port",
                    "database_url_source",
                )
            },
            "local_paths_included": False,
            "filename_values_included": False,
            "external_provider_requests": 0,
        }
    finally:
        session.close()
        engine.dispose()


def _pixiv_source_prior_from_values(*, filename: str | None, source: str | None) -> tuple[str, int, str | None, str] | None:
    for field_name, value in (("source", source), ("filename", filename)):
        text_value = normalize_source_text(value)
        if not text_value:
            continue
        url_match = PIXIV_ARTWORK_RE.search(text_value)
        if url_match:
            page_match = PIXIV_FILE_RE.search(text_value)
            return url_match.group(1), int(page_match.group(2)) if page_match else 0, text_value, field_name
        file_match = PIXIV_FILE_RE.search(text_value)
        if file_match:
            return file_match.group(1), int(file_match.group(2)), None, field_name
    return None


def load_existing_pixiv_artifact_records(*, limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    summary = {
        "attempted": True,
        "record_count": 0,
        "source_kinds": Counter(),
        "canonical_artifacts_read_only": True,
        "exact_local_paths_publicly_redacted": True,
    }

    def add_record(record: Mapping[str, Any], source_kind: str) -> None:
        if len(records) >= limit:
            return
        item = dict(record)
        item.setdefault("provider", "pixiv")
        item.setdefault("data_type_label", DATA_TYPE_ARTIFACT)
        item.setdefault(
            "provenance",
            {
                "source": source_kind,
                "read_only": True,
                "external_provider_request_in_this_run": False,
            },
        )
        records.append(item)
        summary["source_kinds"][source_kind] += 1

    gallery_path = CANONICAL_LOCAL_MANIFESTS / "gallery-dl-pixiv-smoke" / "pixiv-smoke.jsonl"
    for item in _load_gallery_dl_pixiv_dicts(gallery_path):
        work_id = normalize_source_text(item.get("id") or item.get("work_id"))
        if not work_id:
            continue
        page_index = item.get("num", item.get("page_index", 0))
        user = item.get("user") if isinstance(item.get("user"), Mapping) else {}
        tags = item.get("tags") or []
        add_record(
            {
                "provider": "pixiv",
                "provider_record_key": f"canonical-gallery-dl-smoke:pixiv:{work_id}:p{page_index}",
                "source_work_id": work_id,
                "source_page_index": page_index,
                "title": item.get("title"),
                "artist_name": user.get("name") or user.get("account") if isinstance(user, Mapping) else None,
                "artist_id": user.get("id") if isinstance(user, Mapping) else None,
                "tags": tags,
                "metadata_kind": "gallery_dl_pixiv_metadata",
                "data_type_label": DATA_TYPE_ARTIFACT,
                "raw_metadata_json": {
                    "artifact_source_kind": "canonical_gallery_dl_pixiv_smoke",
                    "raw_payload_publicly_redacted": True,
                    "exact_pixiv_id_publicly_redacted": True,
                },
                "provenance": {
                    "source": "canonical_gallery_dl_pixiv_smoke",
                    "read_only": True,
                    "raw_provider_payload_available": True,
                    "external_provider_request_in_this_run": False,
                },
            },
            "canonical_gallery_dl_pixiv_smoke",
        )

    for csv_name, source_kind in (
        ("phase-4.4p1-pixiv-metadata-sheet.csv", "canonical_phase44p1_pixiv_metadata_sheet"),
        ("phase-4.4p1-pixiv-manual-validation-sheet.csv", "canonical_phase44p1_pixiv_manual_validation_sheet"),
    ):
        path = CANONICAL_LOCAL_MANIFESTS / csv_name
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for index, row in enumerate(reader, start=1):
                work_id = normalize_source_text(row.get("pixiv_work_id") or row.get("selected_pixiv_work_id"))
                if not work_id:
                    continue
                tags = _split_tag_text(row.get("tags"))
                artist_name = normalize_source_text(row.get("artist_user_name") or row.get("artist"))
                add_record(
                    {
                        "provider": "pixiv",
                        "provider_record_key": f"artifact:pixiv:{source_kind}:{index}",
                        "source_work_id": work_id,
                        "source_page_index": _safe_int(row.get("page_index"), default=0),
                        "title": normalize_source_text(row.get("title")) or None,
                        "artist_name": artist_name or None,
                        "artist_id": normalize_source_text(row.get("artist_user_id")) or None,
                        "tags": tags,
                        "metadata_kind": "pixiv_phase_report_derived_metadata",
                        "data_type_label": DATA_TYPE_ARTIFACT,
                        "raw_metadata_json": {
                            "artifact_source_kind": source_kind,
                            "raw_provider_payload_available": False,
                            "exact_pixiv_id_publicly_redacted": True,
                        },
                        "provenance": {
                            "source": source_kind,
                            "read_only": True,
                            "raw_provider_payload_available": False,
                            "external_provider_request_in_this_run": False,
                        },
                    },
                    source_kind,
                )
                if len(records) >= limit:
                    break
    summary["record_count"] = len(records)
    summary["source_kinds"] = dict(sorted(summary["source_kinds"].items()))
    return _ensure_data_type_labels(records, default_label=DATA_TYPE_ARTIFACT), summary


def _load_gallery_dl_pixiv_dicts(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload: Any | None = None
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            payload = json.loads(path.read_text(encoding=encoding))
            break
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    if payload is None:
        return []
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            category = normalize_source_text(value.get("category"))
            work_id = normalize_source_text(value.get("id") or value.get("work_id"))
            if work_id and (category.casefold() == "pixiv" or value.get("title") or value.get("user")):
                page = normalize_source_text(value.get("num", value.get("page_index", 0)))
                key = (work_id, page)
                if key not in seen:
                    rows.append(dict(value))
                    seen.add(key)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return rows


def _split_tag_text(value: Any) -> list[str]:
    if isinstance(value, list):
        rows: list[str] = []
        for item in value:
            if isinstance(item, Mapping):
                text_value = item.get("name") or item.get("tag") or item.get("label") or item.get("value")
            else:
                text_value = item
            if not isinstance(text_value, str):
                continue
            normalized = normalize_source_text(text_value)
            if normalized:
                rows.append(normalized)
        return rows
    if not isinstance(value, str):
        return []
    text_value = normalize_source_text(value)
    if not text_value:
        return []
    return [
        normalize_source_text(part)
        for part in re.split(r"[;,|]", text_value)
        if normalize_source_text(part)
    ]


def _safe_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_source_label(value: Any) -> str | None:
    if isinstance(value, Mapping):
        return normalize_source_text(value.get("source")) or normalize_source_text(value.get("metadata_source")) or None
    return normalize_source_text(value) or None


def _candidate_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return normalize_source_text(value) or None


def _candidate_provenance_rank(candidate: SearchableNameCandidate) -> tuple[int, int, int]:
    if candidate.provider == "pixiv" and candidate.data_type_label == DATA_TYPE_REAL and candidate.high_impact:
        return (0, 0, 0)
    if candidate.data_type_label == DATA_TYPE_REAL:
        return (1, 0 if candidate.high_impact else 1, 0)
    if candidate.data_type_label == DATA_TYPE_ARTIFACT:
        return (2, 0 if candidate.high_impact else 1, 0)
    if candidate.parenthetical_outer:
        return (3, 0, 0)
    if candidate.data_type_label == DATA_TYPE_FIXTURE:
        return (5, 0 if candidate.high_impact else 1, 0)
    return (4, 0 if candidate.high_impact else 1, 0)


def _merge_duplicate_candidate(
    existing: SearchableNameCandidate,
    incoming: SearchableNameCandidate,
) -> SearchableNameCandidate:
    occurrence_count = existing.occurrence_count + incoming.occurrence_count
    high_impact = existing.high_impact or incoming.high_impact
    keeper = incoming if _candidate_provenance_rank(incoming) < _candidate_provenance_rank(existing) else existing
    return SearchableNameCandidate(
        **{
            **asdict(keeper),
            "occurrence_count": occurrence_count,
            "high_impact": high_impact,
        }
    )


def load_existing_saucenao_artifact_records(*, limit: int) -> list[dict[str, Any]]:
    path = ROOT / "docs/reports/phase-4.4b1-manual-validation-and-saucenao-metadata-audit-summary.json"
    records: list[dict[str, Any]] = []
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        items = (
            payload.get("metadata_extraction_audit", {}).get("items", [])
            if isinstance(payload, Mapping)
            else []
        )
        for index, item in enumerate(items):
            if len(records) >= limit or not isinstance(item, Mapping):
                continue
            fields_present = any(item.get(key) for key in ("artist", "work_or_copyright", "characters"))
            source_host = normalize_source_text(item.get("source_url_host"))
            if not fields_present and not source_host:
                continue
            records.append(
                {
                    "provider": "saucenao",
                    "provider_record_key": f"artifact:saucenao:b1:{index + 1}",
                    "source_work_id": f"artifact_saucenao_b1_{index + 1}",
                    "similarity": item.get("score"),
                    "artist": item.get("artist"),
                    "work_or_copyright": item.get("work_or_copyright"),
                    "characters": item.get("characters"),
                    "metadata_kind": "saucenao_report_derived_metadata",
                    "data_type_label": DATA_TYPE_ARTIFACT,
                    "raw_metadata_json": {
                        "phase_report": "phase-4.4b1-manual-validation-and-saucenao-metadata-audit-summary.json",
                        "result_class": item.get("result_class"),
                        "score": item.get("score"),
                        "source_url_host": source_host or None,
                        "media_id_included": False,
                    },
                    "provenance": {
                        "source": "existing_public_phase_report",
                        "raw_provider_payload_available": False,
                        "external_provider_request_in_this_run": False,
                    },
                }
            )
    if len(records) < limit:
        for item in _load_local_saucenao_detail_items(limit=limit - len(records)):
            index = len(records) + 1
            material = item.get("material") or item.get("work_or_copyright") or item.get("title")
            source_hosts = item.get("source_url_hosts") if isinstance(item.get("source_url_hosts"), list) else []
            records.append(
                {
                    "provider": "saucenao",
                    "provider_record_key": f"artifact:saucenao:b1-local-detail:{index}",
                    "source_work_id": f"artifact_saucenao_b1_local_detail_{index}",
                    "similarity": item.get("similarity") or item.get("score"),
                    "creator": item.get("creator") or item.get("artist") or item.get("author"),
                    "work_or_copyright": material,
                    "characters": item.get("characters"),
                    "metadata_kind": "saucenao_local_detail_derived_metadata",
                    "data_type_label": DATA_TYPE_ARTIFACT,
                    "raw_metadata_json": {
                        "artifact_source_kind": "phase44b1_local_metadata_extraction_audit_details",
                        "source_url_host_count": len(source_hosts),
                        "raw_provider_payload_available": False,
                        "media_id_included": False,
                    },
                    "provenance": {
                        "source": "existing_local_phase_artifact",
                        "read_only": True,
                        "raw_provider_payload_available": False,
                        "external_provider_request_in_this_run": False,
                    },
                }
            )
    return _ensure_data_type_labels(records[:limit], default_label=DATA_TYPE_ARTIFACT)


def _load_local_saucenao_detail_items(*, limit: int) -> list[dict[str, Any]]:
    path = CANONICAL_LOCAL_MANIFESTS / "phase-4.4b1-metadata-extraction-audit-details.json"
    if limit <= 0 or not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def visit(value: Any) -> None:
        if len(result) >= limit:
            return
        if isinstance(value, Mapping):
            creator = normalize_source_text(value.get("creator") or value.get("artist") or value.get("author"))
            material = normalize_source_text(value.get("material") or value.get("work_or_copyright") or value.get("title"))
            characters = _split_tag_text(value.get("characters"))
            if creator or material or characters:
                key = (creator, material, "|".join(characters))
                if key not in seen:
                    item = dict(value)
                    if characters:
                        item["characters"] = characters
                    if material and not item.get("work_or_copyright"):
                        item["work_or_copyright"] = material
                    result.append(item)
                    seen.add(key)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return result


def generated_scale_fixture_records(*, start_index: int, count: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for offset in range(max(0, count)):
        index = start_index + offset
        selector = index % 5
        if selector == 0:
            rows.append(
                {
                    "provider": "pixiv",
                    "provider_record_key": f"fixture:pixiv:scale:{index}:p0",
                    "source_work_id": f"fixture_pixiv_scale_{index}",
                    "source_page_index": 0,
                    "artist_name": f"Scale Artist {index}",
                    "title": f"Scale Work {index}",
                    "tags": [f"Scale Character {index} (Scale Work {index})", "background"],
                    "metadata_kind": "gallery_dl_pixiv_metadata_fixture",
                    "data_type_label": DATA_TYPE_FIXTURE,
                }
            )
        elif selector == 1:
            rows.append(
                {
                    "provider": "saucenao",
                    "provider_record_key": f"fixture:saucenao:scale:{index}",
                    "creator": f"Scale Sauce Creator {index}",
                    "work_or_copyright": [f"Scale Sauce Work {index}"],
                    "characters": [f"Scale Sauce Character {index} (Scale Sauce Work {index})"],
                    "similarity": 90.0 + (index % 10),
                    "metadata_kind": "saucenao_style_metadata_fixture",
                    "data_type_label": DATA_TYPE_FIXTURE,
                }
            )
        elif selector == 2:
            rows.append(
                {
                    "provider": "danbooru",
                    "provider_record_key": f"fixture:danbooru:numeric:{index}",
                    "tags": [
                        {"name": f"Numeric Fixture Artist {index}", "category": 1},
                        {"name": f"Numeric Fixture Work {index}", "category": 3},
                        {"name": f"Numeric Fixture Character {index}", "category": 4},
                        {"name": f"Numeric Fixture General {index}", "category": 0},
                    ],
                    "metadata_kind": "danbooru_numeric_category_fixture",
                    "data_type_label": DATA_TYPE_FIXTURE,
                }
            )
        elif selector == 3:
            rows.append(
                {
                    "provider": "gelbooru",
                    "provider_record_key": f"fixture:gelbooru:numeric:{index}",
                    "tags": [
                        {"name": f"Gel Fixture Artist {index}", "category": 1},
                        {"name": f"Gel Fixture Work {index}", "category": 3},
                        {"name": f"Gel Fixture Character {index}", "category": 4},
                    ],
                    "metadata_kind": "gelbooru_numeric_category_fixture",
                    "data_type_label": DATA_TYPE_FIXTURE,
                }
            )
        else:
            rows.append(
                {
                    "provider": "no_tag_provider",
                    "provider_record_key": f"fixture:no-tag:scale:{index}",
                    "metadata_kind": "no_tag_no_name_provider_fixture",
                    "data_type_label": DATA_TYPE_FIXTURE,
                }
            )
    return rows


def build_searchable_name_candidates(
    bundle: SourceRegistryBundle,
    records: Sequence[Mapping[str, Any]],
    *,
    max_candidates: int,
) -> list[SearchableNameCandidate]:
    limit = max(0, min(int(max_candidates), SOURCE_ASSERTION_HARD_MAX_CANDIDATES))
    metadata_by_key = {
        provider_record_lookup_key(row.provider, row.provider_record_key): row
        for row in bundle.metadata_records
    }
    raw_record_by_key = {
        provider_record_lookup_key(
            canonical_source_key(row.get("provider") or "generic_provider"),
            normalize_source_text(row.get("provider_record_key")),
        ): row
        for row in records
    }
    tags_by_record: dict[str, list[str]] = defaultdict(list)
    for tag in bundle.tag_observations:
        tags_by_record[provider_record_lookup_key(tag.provider, tag.provider_record_key)].append(tag.raw_tag)

    candidates_by_key: dict[str, SearchableNameCandidate] = {}

    def add_candidate(
        *,
        provider: str,
        provider_record_key: str,
        raw_input: Any,
        data_type_label: str,
        source_kind: str,
        source_field: str | None = None,
        role_hint: str | None = None,
        source_tag_observation_key: str | None = None,
        source_name_observation_key: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        if not isinstance(raw_input, str):
            return
        raw = normalize_source_text(raw_input)
        if not raw:
            return
        normalized = normalize_source_text(raw)
        parenthetical = PARENTHETICAL_CANDIDATE_RE.match(normalized)
        outer = normalize_source_text(parenthetical.group(1)) if parenthetical else None
        inner = normalize_source_text(parenthetical.group(2)) if parenthetical else None
        canonical = canonical_source_key(f"{provider}:{source_kind}:{role_hint or 'unknown'}:{normalized}")[:600]
        candidate_key = f"source-searchable-name-candidate:{canonical}"
        high_impact = (
            provider == "pixiv"
            and data_type_label == DATA_TYPE_REAL
            and (
                source_kind == "source_name_observation"
                or bool(parenthetical)
                or bool(role_hint)
                or len(normalized) >= 2
            )
        )
        record_key = provider_record_lookup_key(provider, provider_record_key)
        raw_record = raw_record_by_key.get(record_key, {})
        artist_name = _candidate_text(raw_record.get("artist_name")) or _candidate_text(raw_record.get("artist"))
        candidate_context = {
            "title": _candidate_text(raw_record.get("title")),
            "artist_name": artist_name,
            "artist_name_present": bool(artist_name),
            "sibling_tags": tags_by_record.get(record_key, [])[:12],
            "parenthetical_outer": outer,
            "parenthetical_inner": inner,
            "source_field": source_field,
            "role_hint": role_hint,
            "data_type_label": data_type_label,
            "source_url_present": bool(_candidate_text(raw_record.get("source_url")) or _candidate_text(raw_record.get("post_url"))),
            "page_count_present": raw_record.get("page_count") is not None,
            "metadata_kind": normalize_source_text(raw_record.get("metadata_kind")) or None,
            "metadata_source": _as_source_label(raw_record.get("provenance")),
            "run_id": normalize_source_text(raw_record.get("provider_run_id")) or None,
        }
        if context:
            candidate_context.update(dict(context))
        incoming = SearchableNameCandidate(
            candidate_key=candidate_key,
            provider=provider,
            provider_record_key=provider_record_key,
            raw_input=raw,
            normalized_input=normalized,
            data_type_label=data_type_label,
            source_kind=source_kind,
            source_field=source_field,
            role_hint=role_hint,
            source_tag_observation_key=source_tag_observation_key,
            source_name_observation_key=source_name_observation_key,
            context=candidate_context,
            parenthetical_outer=outer,
            parenthetical_inner=inner,
            high_impact=high_impact,
        )
        existing = candidates_by_key.get(candidate_key)
        candidates_by_key[candidate_key] = (
            _merge_duplicate_candidate(existing, incoming) if existing is not None else incoming
        )

    for tag in bundle.tag_observations:
        metadata = metadata_by_key[provider_record_lookup_key(tag.provider, tag.provider_record_key)]
        add_candidate(
            provider=tag.provider,
            provider_record_key=tag.provider_record_key,
            raw_input=tag.raw_tag,
            data_type_label=metadata.data_type_label,
            source_kind="source_tag_observation",
            source_field=tag.source_tag_kind,
            role_hint=tag.source_category_raw,
            source_tag_observation_key=tag.observation_key,
            context={
                "source_category_raw": tag.source_category_raw,
                "language_hint": tag.language_hint,
            },
        )

    for name in bundle.name_observations:
        metadata = metadata_by_key[provider_record_lookup_key(name.provider, name.provider_record_key)]
        add_candidate(
            provider=name.provider,
            provider_record_key=name.provider_record_key,
            raw_input=name.raw_name,
            data_type_label=metadata.data_type_label,
            source_kind="source_name_observation",
            source_field=name.source_field,
            role_hint=name.name_role,
            source_name_observation_key=name.observation_key,
            context={
                "source_work_id_present": bool(name.source_work_id),
                "requires_review": name.requires_review,
            },
        )

    for record_key, raw_record in raw_record_by_key.items():
        provider = canonical_source_key(raw_record.get("provider") or "generic_provider")
        if provider != "pixiv":
            continue
        metadata = metadata_by_key.get(record_key)
        if metadata is None:
            continue
        title = _candidate_text(raw_record.get("title"))
        if not title:
            continue
        add_candidate(
            provider=provider,
            provider_record_key=normalize_source_text(raw_record.get("provider_record_key")),
            raw_input=title,
            data_type_label=metadata.data_type_label,
            source_kind="source_title_candidate",
            source_field="pixiv_source_title",
            role_hint="source_title",
            context={
                "title_candidate_requires_llm_evidence": True,
                "title_is_not_deterministic_work_title_identity": True,
                "tag_count": len(tags_by_record.get(record_key, [])),
            },
        )

    def priority(candidate: SearchableNameCandidate) -> tuple[int, int, str]:
        if candidate.provider == "pixiv" and candidate.data_type_label == DATA_TYPE_REAL and candidate.high_impact:
            bucket = 0
        elif candidate.provider == "pixiv" and candidate.data_type_label == DATA_TYPE_REAL:
            bucket = 1
        elif candidate.provider == "pixiv" and candidate.data_type_label == DATA_TYPE_ARTIFACT:
            bucket = 2
        elif candidate.provider == "saucenao" and candidate.data_type_label in {DATA_TYPE_REAL, DATA_TYPE_ARTIFACT}:
            bucket = 3
        elif candidate.parenthetical_outer:
            bucket = 4
        elif candidate.data_type_label == DATA_TYPE_FIXTURE:
            bucket = 8
        else:
            bucket = 6
        return bucket, -candidate.occurrence_count, candidate.candidate_key

    sorted_candidates = sorted(candidates_by_key.values(), key=priority)
    if len(sorted_candidates) <= limit:
        return sorted_candidates

    selected: dict[str, SearchableNameCandidate] = {}

    def add_quota(predicate, quota: int) -> None:
        if quota <= 0 or len(selected) >= limit:
            return
        added = 0
        for candidate in sorted_candidates:
            if len(selected) >= limit or added >= quota:
                return
            if candidate.candidate_key in selected or not predicate(candidate):
                continue
            selected[candidate.candidate_key] = candidate
            added += 1

    real_pixiv_quota = min(limit, max(REAL_PIXIV_APPLICABLE_NAME_SIGNAL_TARGET * 4, 80))
    saucenao_quota = min(max(SAUCENAO_ARTIFACT_RECORD_TARGET * 3, 30), max(0, limit - real_pixiv_quota))
    pixiv_artifact_quota = min(20, max(0, limit - real_pixiv_quota - saucenao_quota))

    add_quota(
        lambda candidate: candidate.provider == "pixiv"
        and candidate.data_type_label == DATA_TYPE_REAL
        and candidate.high_impact,
        real_pixiv_quota,
    )
    add_quota(
        lambda candidate: candidate.provider == "saucenao"
        and candidate.data_type_label in {DATA_TYPE_REAL, DATA_TYPE_ARTIFACT}
        and (candidate.role_hint in {"artist", "creator", "work_title"} or candidate.source_field == "saucenao_work_or_copyright"),
        saucenao_quota,
    )
    add_quota(
        lambda candidate: candidate.provider == "pixiv" and candidate.data_type_label == DATA_TYPE_ARTIFACT,
        pixiv_artifact_quota,
    )
    add_quota(lambda _candidate: True, limit - len(selected))
    return list(selected.values())[:limit]


def _safe_url_host(url: str) -> str | None:
    try:
        return urlparse(url).hostname or None
    except Exception:
        return None


def source_assertion_provider_from_env() -> tuple[BaseLLMProvider | None, dict[str, Any]]:
    fallback_enabled = bool(settings.TAG_TRANSLATION_LLM_FALLBACK_ENABLED)
    fallback_provider_type = settings.TAG_TRANSLATION_LLM_FALLBACK_PROVIDER
    fallback_key = settings.TAG_TRANSLATION_LLM_FALLBACK_API_KEY
    fallback_model = settings.TAG_TRANSLATION_LLM_FALLBACK_MODEL
    fallback_url = settings.TAG_TRANSLATION_LLM_FALLBACK_BASE_URL
    summary = {
        "provider": "fallback_only",
        "llm_provider_label": "fallback",
        "fallback_provider_type": fallback_provider_type,
        "model_label": "fallback_model_configured" if fallback_model else "unknown",
        "model_name_redacted": bool(fallback_model),
        "fallback_enabled": fallback_enabled,
        "llm_access_configured": bool(fallback_key and fallback_model and fallback_url),
        "llm_access_stored": False,
        "base_url_redacted": True,
        "uses_primary_model": False,
        "uses_fallback_provider": True,
    }
    if not fallback_enabled:
        return None, {**summary, "unavailable_reason": "fallback_disabled"}
    if fallback_provider_type not in {"openai_compatible", "deepseek"}:
        return None, {**summary, "unavailable_reason": "fallback_provider_not_openai_compatible"}
    if not (fallback_key and fallback_model and fallback_url):
        return None, {**summary, "unavailable_reason": "fallback_config_incomplete"}
    primary_disabled = OpenAICompatibleProvider(
        api_key="",
        model="",
        base_url=fallback_url,
        label="primary_disabled_for_f5_source_assertion",
    )
    fallback = OpenAICompatibleProvider(
        api_key=fallback_key,
        model=fallback_model,
        base_url=fallback_url,
        label="fallback",
    )
    provider = FallbackProvider(primary_disabled, fallback)
    return provider if provider.is_available() else None, summary


def is_retriable_source_assertion_llm_error(exc: LLMProviderError) -> bool:
    if isinstance(exc, LLMTransportError):
        return True
    if isinstance(exc, LLMHTTPStatusError):
        return bool(exc.should_fallback)
    if isinstance(exc, LLMBatchAggregateError):
        return bool(exc.all_fallback_eligible_errors)
    if isinstance(exc, LLMAllProvidersFailed):
        fallback_error = getattr(exc, "fallback_error", None)
        primary_error = getattr(exc, "primary_error", None)
        return any(
            isinstance(error, (LLMTransportError, LLMBatchAggregateError))
            and (
                isinstance(error, LLMTransportError)
                or (isinstance(error, LLMBatchAggregateError) and bool(error.all_fallback_eligible_errors))
            )
            or (isinstance(error, LLMHTTPStatusError) and bool(error.should_fallback))
            for error in (primary_error, fallback_error)
            if error is not None
        )
    return False


def _host_resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, None)
        return True
    except OSError:
        return False


def load_f5_project_config() -> f1.ProjectConfig:
    config = f1.load_project_config(ROOT)
    process_host = normalize_source_text(os.environ.get("POSTGRES_HOST"))
    if config.db_host == "db" and process_host == "localhost" and not _host_resolves("db"):
        return replace(
            config,
            db_host="localhost",
            database_url=config.database_url.set(host="localhost"),
            database_url_source=f"{config.database_url_source}+process_host_override",
        )
    return config


def candidate_prompt_payload(candidate: SearchableNameCandidate) -> dict[str, Any]:
    return {
        "candidate_key": candidate.candidate_key,
        "provider": candidate.provider,
        "input": candidate.raw_input,
        "normalized_input": candidate.normalized_input,
        "source_kind": candidate.source_kind,
        "source_field": candidate.source_field,
        "role_hint": candidate.role_hint,
        "data_type_label": candidate.data_type_label,
        "parenthetical_outer": candidate.parenthetical_outer,
        "parenthetical_inner": candidate.parenthetical_inner,
        "context": candidate.context,
        "must_not_create_entity_truth": True,
    }


def source_assertion_system_prompt() -> str:
    return (
        "You classify anime/illustration provider tags and provider name fields into searchable source-level "
        "identity assertions. These assertions are NOT Entity truth, NOT EntityAlias, NOT confirmed assignments.\n"
        "Return ONLY a valid JSON array. Each output object must include exactly the supplied candidate_key and these fields: "
        "input, normalized_input, is_name_like, asserted_role, extracted_name, base_name, work_context, "
        "alias_candidates, is_searchable_identity, searchable_status, confidence, reason_code, evidence_summary, "
        "requires_review, should_not_be_entity_truth.\n"
        f"Allowed asserted_role values: {sorted(ASSERTION_ROLES)}.\n"
        f"Allowed searchable_status values: {sorted(ASSERTION_STATUSES)}.\n"
        f"Allowed confidence values: {sorted(ASSERTION_CONFIDENCES)}.\n"
        f"Allowed reason_code values: {sorted(ASSERTION_REASON_CODES)}.\n"
        "Use searchable_active only when the tag/name is a plausible searchable source identity at source-search level. "
        "Use rejected for clearly descriptive/generic/not-name-like/popularity markers. Use unresolved or needs_review for ambiguity. "
        "For parenthetical character-work tags, extract the outer name and work_context when supported by the text. "
        "For Pixiv artwork titles, use asserted_role=source_title unless surrounding tags/artist/source context strongly supports work_title; "
        "never treat a title alone as Entity truth. "
        "Set should_not_be_entity_truth to true for every row. Do not include hidden reasoning; evidence_summary must be concise. "
        "The response must start with '[' and end with ']'."
    )


def _classification_input_payload(candidates: Sequence[SearchableNameCandidate]) -> dict[str, Any]:
    run_ids = sorted(
        {
            normalize_source_text(candidate.context.get("run_id"))
            for candidate in candidates
            if normalize_source_text(candidate.context.get("run_id"))
        }
    )
    return {
        "run_id": run_ids[0] if len(run_ids) == 1 else None,
        "run_ids": run_ids,
        "prompt_version": SOURCE_ASSERTION_PROMPT_VERSION,
        "structured_output_schema_version": SOURCE_ASSERTION_SCHEMA_VERSION,
        "candidate_count": len(candidates),
        "candidates": [candidate_prompt_payload(candidate) for candidate in candidates],
    }


def _classification_messages(input_payload: Mapping[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": source_assertion_system_prompt()},
        {
            "role": "user",
            "content": json.dumps(f1._coerce_json_safe(input_payload), ensure_ascii=False, sort_keys=True),
        },
    ]


def _strip_json_fence(content: str) -> str:
    text_value = content.strip()
    if text_value.startswith("```"):
        lines = text_value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text_value = "\n".join(lines).strip()
    return text_value


def _extract_json_array_text(content: str) -> str:
    text_value = _strip_json_fence(content)
    if text_value.startswith("[") and text_value.endswith("]"):
        return text_value
    start = text_value.find("[")
    end = text_value.rfind("]")
    if start >= 0 and end > start:
        return text_value[start : end + 1]
    return text_value


def _parse_llm_json_array(content: str) -> list[Any]:
    text_value = _extract_json_array_text(content)
    try:
        payload = json.loads(text_value)
    except json.JSONDecodeError as exc:
        raise Phase44P2RF5Error("source_searchable_name_assertion_invalid_json") from exc
    if not isinstance(payload, list):
        raise Phase44P2RF5Error("source_searchable_name_assertion_non_array_response")
    return payload


def _coerce_classification_response(parsed: Any, *, expected_count: int) -> tuple[list[Any], str | None]:
    if isinstance(parsed, list):
        return parsed, None
    if isinstance(parsed, Mapping):
        for key in ("assertions", "results", "items", "outputs", "candidates"):
            value = parsed.get(key)
            if isinstance(value, list):
                return value, f"top_level_{key}_array"
        if expected_count == 1 and "candidate_key" in parsed:
            return [parsed], "single_object_wrapped"
    raise Phase44P2RF5Error("source_searchable_name_assertion_non_array_response")


async def _classify_candidate_chunk(
    provider: BaseLLMProvider,
    candidates: Sequence[SearchableNameCandidate],
    *,
    max_tokens: int = 6000,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    input_payload = _classification_input_payload(candidates)
    messages = _classification_messages(input_payload)
    output_payload: dict[str, Any] = {
        "raw_response_stored": False,
        "response_normalization": None,
        "repair_strategy": "complete_json",
    }
    try:
        parsed = await provider.complete_json(messages, temperature=0.0, max_tokens=max_tokens)
        parsed_items, normalization = _coerce_classification_response(parsed, expected_count=len(candidates))
    except LLMResponseFormatError:
        repair_content = await provider.complete_chat(messages, temperature=0.0, max_tokens=max_tokens)
        parsed_items = _parse_llm_json_array(repair_content)
        normalization = "json_array_extracted_after_complete_json_failure"
        output_payload["repair_strategy"] = "complete_chat_json_array_extract"
    output_payload["parsed_response"] = parsed_items
    output_payload["response_normalization"] = normalization
    return parsed_items, input_payload, output_payload


def validate_model_assertion_output(
    item: Mapping[str, Any],
    candidate: SearchableNameCandidate,
) -> ValidatedModelAssertion:
    if not isinstance(item, Mapping):
        raise Phase44P2RF5Error("source_searchable_name_assertion_schema_invalid:not_object")
    candidate_key = normalize_source_text(item.get("candidate_key"))
    if not candidate_key:
        raise Phase44P2RF5Error("source_searchable_name_assertion_schema_invalid:candidate_key_missing")
    if candidate_key != candidate.candidate_key:
        raise Phase44P2RF5Error("source_searchable_name_assertion_schema_invalid:candidate_key_mismatch")
    raw_input = normalize_source_text(item.get("input"))
    normalized_input = normalize_source_text(item.get("normalized_input"))
    if raw_input != candidate.raw_input:
        raise Phase44P2RF5Error("source_searchable_name_assertion_schema_invalid:input_mismatch")
    if normalized_input != candidate.normalized_input and canonical_source_key(normalized_input) != canonical_source_key(
        candidate.normalized_input
    ):
        raise Phase44P2RF5Error("source_searchable_name_assertion_schema_invalid:normalized_input_mismatch")
    normalized_input = candidate.normalized_input

    role = normalize_source_text(item.get("asserted_role"))
    status = normalize_source_text(item.get("searchable_status"))
    confidence = normalize_source_text(item.get("confidence"))
    reason_code = normalize_source_text(item.get("reason_code"))
    if role not in ASSERTION_ROLES:
        raise Phase44P2RF5Error("source_searchable_name_assertion_schema_invalid:asserted_role")
    if status not in ASSERTION_STATUSES:
        raise Phase44P2RF5Error("source_searchable_name_assertion_schema_invalid:searchable_status")
    if confidence not in ASSERTION_CONFIDENCES:
        raise Phase44P2RF5Error("source_searchable_name_assertion_schema_invalid:confidence")
    if reason_code not in ASSERTION_REASON_CODES:
        raise Phase44P2RF5Error("source_searchable_name_assertion_schema_invalid:reason_code")
    for bool_key in ("is_name_like", "is_searchable_identity", "requires_review", "should_not_be_entity_truth"):
        if not isinstance(item.get(bool_key), bool):
            raise Phase44P2RF5Error(f"source_searchable_name_assertion_schema_invalid:{bool_key}")
    if item.get("should_not_be_entity_truth") is not True:
        raise Phase44P2RF5Error("source_searchable_name_assertion_schema_invalid:entity_truth_flag")
    aliases = item.get("alias_candidates")
    if not isinstance(aliases, list) or not all(isinstance(value, str) for value in aliases):
        raise Phase44P2RF5Error("source_searchable_name_assertion_schema_invalid:alias_candidates")
    if status == "searchable_active" and item.get("is_searchable_identity") is not True:
        raise Phase44P2RF5Error("source_searchable_name_assertion_schema_invalid:active_not_searchable")
    if status == "searchable_active" and reason_code in NON_SEARCHABLE_REJECT_REASON_CODES:
        raise Phase44P2RF5Error("source_searchable_name_assertion_schema_invalid:active_contradictory_reason")
    return ValidatedModelAssertion(
        input=raw_input,
        normalized_input=normalized_input,
        is_name_like=bool(item["is_name_like"]),
        asserted_role=role,
        extracted_name=normalize_source_text(item.get("extracted_name")) or None,
        base_name=normalize_source_text(item.get("base_name")) or None,
        work_context=normalize_source_text(item.get("work_context")) or None,
        alias_candidates=tuple(normalize_source_text(value) for value in aliases if normalize_source_text(value)),
        is_searchable_identity=bool(item["is_searchable_identity"]),
        searchable_status=status,
        confidence=confidence,
        reason_code=reason_code,
        evidence_summary=normalize_source_text(item.get("evidence_summary"))[:1000],
        requires_review=bool(item["requires_review"]),
        should_not_be_entity_truth=True,
    )


def assertion_draft_from_model_output(
    candidate: SearchableNameCandidate,
    output: ValidatedModelAssertion,
    *,
    model_name: str,
) -> SourceSearchableNameAssertionDraft:
    asserted_name = output.extracted_name or output.base_name or candidate.parenthetical_outer or output.input
    canonical_name_key = canonical_source_key(asserted_name or output.normalized_input) or canonical_source_key(output.input)
    confidence_score = {"high": 0.92, "medium": 0.74, "low": 0.45}[output.confidence]
    return SourceSearchableNameAssertionDraft(
        provider=candidate.provider,
        provider_record_key=candidate.provider_record_key,
        assertion_key=f"source-searchable-name-assertion:{canonical_source_key(candidate.candidate_key)[:650]}",
        raw_input=candidate.raw_input,
        normalized_input=candidate.normalized_input,
        canonical_name_key=canonical_name_key,
        asserted_name=asserted_name,
        asserted_role=output.asserted_role,
        status=output.searchable_status,
        confidence=output.confidence,
        confidence_score=confidence_score,
        evidence_sources_json={
            "source_kind": candidate.source_kind,
            "source_field": candidate.source_field,
            "reason_code": output.reason_code,
            "is_name_like": output.is_name_like,
            "is_searchable_identity": output.is_searchable_identity,
            "alias_candidates": list(output.alias_candidates),
            "work_context": output.work_context,
        },
        model_name=model_name,
        prompt_version=SOURCE_ASSERTION_PROMPT_VERSION,
        structured_output_schema_version=SOURCE_ASSERTION_SCHEMA_VERSION,
        reasoning_summary_private=output.evidence_summary,
        provenance_summary={
            "provider": candidate.provider,
            "data_type_label": candidate.data_type_label,
            "llm_structured_classification": True,
            "should_not_be_entity_truth": True,
            "image_uploaded": False,
            "api_called_per_unique_candidate": True,
        },
        requires_review=output.requires_review,
        source_tag_observation_key=candidate.source_tag_observation_key,
        source_name_observation_key=candidate.source_name_observation_key,
    )


def _assertion_review_row(
    candidate: SearchableNameCandidate,
    output: ValidatedModelAssertion | None,
    *,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "candidate_key": candidate.candidate_key,
        "provider": candidate.provider,
        "data_type_label": candidate.data_type_label,
        "source_kind": candidate.source_kind,
        "source_field": candidate.source_field,
        "role_hint": candidate.role_hint,
        "raw_input": candidate.raw_input,
        "normalized_input": candidate.normalized_input,
        "parenthetical_outer": candidate.parenthetical_outer,
        "parenthetical_inner": candidate.parenthetical_inner,
        "high_impact": candidate.high_impact,
        "status": output.searchable_status if output else None,
        "asserted_role": output.asserted_role if output else None,
        "extracted_name": output.extracted_name if output else None,
        "base_name": output.base_name if output else None,
        "work_context": output.work_context if output else None,
        "confidence": output.confidence if output else None,
        "reason_code": output.reason_code if output else None,
        "requires_review": output.requires_review if output else None,
        "validation_error": error,
    }


def _error_bucket(exc: BaseException) -> str:
    text_value = normalize_source_text(str(exc))
    if text_value.startswith("source_searchable_name_assertion_schema_invalid:"):
        return text_value
    if text_value.startswith("source_searchable_name_assertion_"):
        return text_value
    return type(exc).__name__


def unresolved_assertion_draft_from_model_failure(
    candidate: SearchableNameCandidate,
    *,
    model_name: str,
    error_bucket: str,
    recovery_strategy: str,
) -> SourceSearchableNameAssertionDraft:
    asserted_name = candidate.parenthetical_outer or candidate.normalized_input or candidate.raw_input
    canonical_name_key = canonical_source_key(asserted_name) or canonical_source_key(candidate.raw_input)
    return SourceSearchableNameAssertionDraft(
        provider=candidate.provider,
        provider_record_key=candidate.provider_record_key,
        assertion_key=f"source-searchable-name-assertion:{canonical_source_key(candidate.candidate_key)[:650]}",
        raw_input=candidate.raw_input,
        normalized_input=candidate.normalized_input,
        canonical_name_key=canonical_name_key,
        asserted_name=asserted_name,
        asserted_role="unknown",
        status="unresolved",
        confidence="low",
        confidence_score=0.0,
        evidence_sources_json={
            "source_kind": candidate.source_kind,
            "source_field": candidate.source_field,
            "reason_code": "model_output_invalid",
            "model_output_invalid": True,
            "error_bucket": error_bucket,
            "recovery_strategy": recovery_strategy,
            "is_searchable_identity": False,
        },
        model_name=model_name,
        prompt_version=SOURCE_ASSERTION_PROMPT_VERSION,
        structured_output_schema_version=SOURCE_ASSERTION_SCHEMA_VERSION,
        reasoning_summary_private=f"Model output could not be validated safely: {error_bucket}",
        provenance_summary={
            "provider": candidate.provider,
            "data_type_label": candidate.data_type_label,
            "llm_structured_classification": True,
            "llm_output_downgraded_to_unresolved": True,
            "should_not_be_entity_truth": True,
            "image_uploaded": False,
            "api_called_per_unique_candidate": True,
        },
        requires_review=True,
        source_tag_observation_key=candidate.source_tag_observation_key,
        source_name_observation_key=candidate.source_name_observation_key,
    )


def provider_field_assertion_output(candidate: SearchableNameCandidate) -> ValidatedModelAssertion | None:
    if candidate.provider != "saucenao" or candidate.source_kind != "source_name_observation":
        return None
    role = candidate.role_hint
    if candidate.source_field == "saucenao_work_or_copyright":
        role = "work_title"
    if role not in {"artist", "creator", "work_title"}:
        return None
    reason_code = "known_artist_creator" if role in {"artist", "creator"} else "known_work_title"
    return ValidatedModelAssertion(
        input=candidate.raw_input,
        normalized_input=candidate.normalized_input,
        is_name_like=True,
        asserted_role=role,
        extracted_name=candidate.normalized_input,
        base_name=candidate.normalized_input,
        work_context=None,
        alias_candidates=(),
        is_searchable_identity=True,
        searchable_status="searchable_active",
        confidence="high",
        reason_code=reason_code,
        evidence_summary="High-confidence SauceNAO provider field retained as a source-search assertion.",
        requires_review=True,
        should_not_be_entity_truth=True,
    )


def classify_source_searchable_name_assertions(
    args: argparse.Namespace,
    bundle: SourceRegistryBundle,
    records: Sequence[Mapping[str, Any]],
) -> tuple[list[SearchableNameCandidate], list[SourceSearchableNameAssertionDraft], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    candidates = build_searchable_name_candidates(
        bundle,
        records,
        max_candidates=int(args.source_assertion_max_candidates),
    )
    base_summary: dict[str, Any] = {
        "prompt_version": SOURCE_ASSERTION_PROMPT_VERSION,
        "structured_output_schema_version": SOURCE_ASSERTION_SCHEMA_VERSION,
        "candidate_count": len(candidates),
        "default_max_candidates": SOURCE_ASSERTION_DEFAULT_MAX_CANDIDATES,
        "hard_max_candidates": SOURCE_ASSERTION_HARD_MAX_CANDIDATES,
        "api_call_attempted": False,
        "llm_access_stored": False,
        "image_uploads": 0,
        "per_image_api_calls": 0,
        "mode": "no_api_preflight",
        "outputs_valid": 0,
        "invalid_outputs": 0,
        "refusal_or_blocked": 0,
        "searchable_active": 0,
        "unresolved": 0,
        "rejected": 0,
        "needs_review": 0,
        "api_call_attempts": 0,
        "api_chunks_attempted": 0,
        "chunk_retries": 0,
        "chunk_split_recoveries": 0,
        "single_candidate_failures_downgraded": 0,
        "provider_field_assertions": 0,
        "llm_candidate_count": 0,
        "invalid_output_reason_counts": {},
        "repair_strategies_attempted": [],
    }
    if not args.use_llm_api:
        base_summary["mode"] = "api_not_requested"
        return candidates, [], base_summary, [], [], []
    if args.no_db or args.dry_run:
        base_summary["mode"] = "api_skipped_no_db_or_dry_run"
        return candidates, [], base_summary, [], [], []
    if not candidates:
        base_summary["mode"] = "api_not_called_no_candidates"
        return candidates, [], base_summary, [], [], []

    provider_field_rows: list[tuple[SearchableNameCandidate, ValidatedModelAssertion]] = []
    llm_candidates: list[SearchableNameCandidate] = []
    for candidate in candidates:
        provider_output = provider_field_assertion_output(candidate)
        if provider_output is None:
            llm_candidates.append(candidate)
        else:
            provider_field_rows.append((candidate, provider_output))
    base_summary["provider_field_assertions"] = len(provider_field_rows)
    base_summary["llm_candidate_count"] = len(llm_candidates)

    provider: BaseLLMProvider | None = None
    if llm_candidates:
        provider, provider_summary = source_assertion_provider_from_env()
        base_summary.update(provider_summary)
        if provider is None:
            raise Phase44P2RF5Error("source_searchable_name_assertion_api_unavailable")

        base_summary["api_call_attempted"] = True
        base_summary["mode"] = "llm_api_structured_json_validation"
    else:
        base_summary["mode"] = "provider_field_assertions_only"
    chunks = [
        llm_candidates[index : index + int(args.source_assertion_chunk_size)]
        for index in range(0, len(llm_candidates), int(args.source_assertion_chunk_size))
    ]
    inputs_private: list[dict[str, Any]] = []
    outputs_private: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    drafts: list[SourceSearchableNameAssertionDraft] = []
    invalid_reasons: Counter[str] = Counter()
    repair_strategies: set[str] = set()

    for candidate, validated in provider_field_rows:
        draft = assertion_draft_from_model_output(
            candidate,
            validated,
            model_name="provider_field_saucenao",
        )
        drafts.append(draft)
        review_rows.append(_assertion_review_row(candidate, validated))
        base_summary["outputs_valid"] += 1
        base_summary[validated.searchable_status] += 1

    def record_failed_attempt(
        chunk: Sequence[SearchableNameCandidate],
        exc: BaseException,
        *,
        strategy: str,
    ) -> None:
        repair_strategies.add(strategy)
        bucket = _error_bucket(exc)
        invalid_reasons[bucket] += len(chunk)
        inputs_private.append({**_classification_input_payload(chunk), "repair_strategy": strategy})
        outputs_private.append(
            {
                "candidate_count": len(chunk),
                "parsed_response": None,
                "raw_response_stored": False,
                "error_bucket": bucket,
                "error_type": type(exc).__name__,
                "repair_strategy": strategy,
            }
        )

    def downgrade_single_candidate(
        candidate: SearchableNameCandidate,
        exc: BaseException,
        *,
        strategy: str,
    ) -> None:
        bucket = _error_bucket(exc)
        draft = unresolved_assertion_draft_from_model_failure(
            candidate,
            model_name=str(base_summary.get("model_label") or ""),
            error_bucket=bucket,
            recovery_strategy=strategy,
        )
        drafts.append(draft)
        review_rows.append(_assertion_review_row(candidate, None, error=bucket))
        base_summary["invalid_outputs"] += 1
        base_summary["unresolved"] += 1
        base_summary["single_candidate_failures_downgraded"] += 1

    async def classify_chunk_with_repair(
        chunk: Sequence[SearchableNameCandidate],
        *,
        strategy: str,
    ) -> None:
        base_summary["api_chunks_attempted"] += 1
        last_error: BaseException | None = None
        for attempt in range(int(args.source_assertion_api_retries) + 1):
            try:
                base_summary["api_call_attempts"] += 1
                parsed, input_payload, output_payload = await _classify_candidate_chunk(
                    provider,
                    chunk,
                    max_tokens=int(args.source_assertion_max_tokens),
                )
                if len(parsed) != len(chunk):
                    raise Phase44P2RF5Error("source_searchable_name_assertion_schema_invalid:response_count_mismatch")
                validated_rows: list[tuple[SearchableNameCandidate, ValidatedModelAssertion]] = []
                for candidate, item in zip(chunk, parsed):
                    validated_rows.append((candidate, validate_model_assertion_output(item, candidate)))
                inputs_private.append({**input_payload, "repair_strategy": strategy})
                outputs_private.append({**output_payload, "repair_strategy": strategy})
                repair_strategies.add(strategy)
                for candidate, validated in validated_rows:
                    draft = assertion_draft_from_model_output(
                        candidate,
                        validated,
                        model_name=str(base_summary.get("model_label") or ""),
                    )
                    drafts.append(draft)
                    review_rows.append(_assertion_review_row(candidate, validated))
                    base_summary["outputs_valid"] += 1
                    base_summary[validated.searchable_status] += 1
                return
            except LLMProviderError as exc:
                if isinstance(exc, LLMResponseFormatError) or is_retriable_source_assertion_llm_error(exc):
                    last_error = exc
                else:
                    raise
            except Phase44P2RF5Error as exc:
                last_error = exc
            if attempt < int(args.source_assertion_api_retries):
                base_summary["chunk_retries"] += 1

        if last_error is None:
            last_error = Phase44P2RF5Error("source_searchable_name_assertion_api_retry_failed")
        record_failed_attempt(chunk, last_error, strategy=strategy)
        if len(chunk) > 1:
            base_summary["chunk_split_recoveries"] += 1
            midpoint = max(1, len(chunk) // 2)
            await classify_chunk_with_repair(chunk[:midpoint], strategy="split_after_invalid_output")
            await classify_chunk_with_repair(chunk[midpoint:], strategy="split_after_invalid_output")
            return
        downgrade_single_candidate(chunk[0], last_error, strategy="single_candidate_unresolved_after_retries")

    async def classify_all() -> None:
        for chunk in chunks:
            await classify_chunk_with_repair(chunk, strategy="initial_chunk")

    try:
        if chunks:
            asyncio.run(classify_all())
    except LLMProviderError as exc:
        raise Phase44P2RF5Error(f"source_searchable_name_assertion_api_failed:{type(exc).__name__}") from None

    base_summary["invalid_output_reason_counts"] = dict(sorted(invalid_reasons.items()))
    base_summary["repair_strategies_attempted"] = sorted(repair_strategies)

    return candidates, drafts, base_summary, inputs_private, outputs_private, review_rows


def source_assertion_llm_preflight(args: argparse.Namespace) -> dict[str, Any]:
    provider, provider_summary = source_assertion_provider_from_env()
    if provider is None:
        raise Phase44P2RF5Error("llm_provider_not_configured")
    candidate = SearchableNameCandidate(
        candidate_key="preflight:source-searchable-name-assertion",
        provider="pixiv",
        provider_record_key="preflight:pixiv:source-name-assertion",
        raw_input="Hero Name(Work Name)",
        normalized_input="Hero Name(Work Name)",
        data_type_label=DATA_TYPE_FIXTURE,
        source_kind="source_tag_observation",
        source_field="preflight",
        role_hint=None,
        context={
            "title": "Work Name",
            "sibling_tags": ["Hero Name(Work Name)", "blue hair"],
            "preflight_only": True,
        },
        parenthetical_outer="Hero Name",
        parenthetical_inner="Work Name",
        high_impact=False,
    )
    try:
        parsed, _input_payload, _output_payload = asyncio.run(
            _classify_candidate_chunk(
                provider,
                [candidate],
                max_tokens=min(int(args.source_assertion_max_tokens), 2000),
            )
        )
    except LLMProviderError as exc:
        raise Phase44P2RF5Error(f"source_assertion_preflight_failed:{type(exc).__name__}") from None
    if len(parsed) != 1:
        raise Phase44P2RF5Error("source_assertion_preflight_response_count_mismatch")
    validated = validate_model_assertion_output(parsed[0], candidate)
    return {
        "mode": "llm_fallback_complete_json_preflight",
        "success": True,
        "provider": {
            "llm_provider_label": provider_summary.get("llm_provider_label"),
            "fallback_provider_type": provider_summary.get("fallback_provider_type"),
            "model_label": provider_summary.get("model_label"),
            "uses_primary_model": provider_summary.get("uses_primary_model"),
            "uses_fallback_provider": provider_summary.get("uses_fallback_provider"),
            "base_url_redacted": True,
            "llm_access_configured": provider_summary.get("llm_access_configured"),
            "llm_access_stored": False,
        },
        "schema": {
            "prompt_version": SOURCE_ASSERTION_PROMPT_VERSION,
            "structured_output_schema_version": SOURCE_ASSERTION_SCHEMA_VERSION,
            "output_valid": True,
            "status": validated.searchable_status,
            "role": validated.asserted_role,
            "should_not_be_entity_truth": validated.should_not_be_entity_truth,
        },
        "db_write": False,
        "image_upload": False,
    }


def source_searchable_assertion_coverage_summary(
    candidates: Sequence[SearchableNameCandidate],
    assertions: Sequence[SourceSearchableNameAssertionDraft],
) -> dict[str, Any]:
    by_key = {
        assertion.assertion_key.replace("source-searchable-name-assertion:", ""): assertion
        for assertion in assertions
    }
    # The persisted assertion key is canonicalized; use raw tuple lookup for coverage instead.
    assertion_by_input = {
        (row.provider, row.normalized_input, row.raw_input): row
        for row in assertions
    }

    def assertion_for(candidate: SearchableNameCandidate) -> SourceSearchableNameAssertionDraft | None:
        return assertion_by_input.get((candidate.provider, candidate.normalized_input, candidate.raw_input))

    def terminal(assertion: SourceSearchableNameAssertionDraft | None) -> bool:
        if assertion is None:
            return False
        if assertion.status == "searchable_active":
            return True
        if assertion.status != "rejected":
            return False
        reason_code = (assertion.evidence_sources_json or {}).get("reason_code")
        return reason_code in NON_SEARCHABLE_REJECT_REASON_CODES

    real_pixiv_high = [
        candidate
        for candidate in candidates
        if candidate.provider == "pixiv" and candidate.data_type_label == DATA_TYPE_REAL and candidate.high_impact
    ]
    parenthetical = [
        candidate
        for candidate in real_pixiv_high
        if candidate.parenthetical_outer and candidate.parenthetical_inner
    ]
    saucenao = [
        candidate
        for candidate in candidates
        if candidate.provider == "saucenao"
        and candidate.data_type_label in {DATA_TYPE_REAL, DATA_TYPE_ARTIFACT}
        and (candidate.role_hint in {"artist", "creator", "work_title"} or candidate.source_field == "saucenao_work_or_copyright")
    ]

    def group_summary(rows: Sequence[SearchableNameCandidate]) -> dict[str, Any]:
        active = 0
        terminal_count = 0
        unresolved_like = 0
        missing = 0
        for candidate in rows:
            assertion = assertion_for(candidate)
            if assertion is None:
                missing += 1
                unresolved_like += 1
                continue
            if assertion.status == "searchable_active":
                active += 1
            if terminal(assertion):
                terminal_count += 1
            if assertion.status in {"unresolved", "needs_review"}:
                unresolved_like += 1
        return {
            "candidate_count": len(rows),
            "searchable_active_count": active,
            "terminal_active_or_valid_rejected_count": terminal_count,
            "missing_assertion_count": missing,
            "unresolved_or_needs_review_count": unresolved_like,
            "coverage": round(terminal_count / len(rows), 4) if rows else None,
            "unresolved_rate": round(unresolved_like / len(rows), 4) if rows else None,
        }

    real_summary = group_summary(real_pixiv_high)
    parenthetical_summary = group_summary(parenthetical)
    saucenao_summary = group_summary(saucenao)
    saucenao_candidate_supply_target = min(SAUCENAO_ARTIFACT_RECORD_TARGET, len(saucenao))
    saucenao_candidate_supply_sufficient = len(saucenao) >= saucenao_candidate_supply_target and len(saucenao) > 0
    real_pixiv_supply_sufficient = len(real_pixiv_high) >= REAL_PIXIV_APPLICABLE_NAME_SIGNAL_TARGET
    real_pixiv_unresolved_rate = real_summary["unresolved_rate"]
    real_pixiv_target_met = (
        real_summary["coverage"] is not None
        and real_summary["coverage"] >= 0.8
        and real_pixiv_unresolved_rate is not None
        and real_pixiv_unresolved_rate <= 0.2
        and real_pixiv_supply_sufficient
    )
    parenthetical_target_met = (
        not parenthetical
        or (
            parenthetical_summary["coverage"] is not None
            and parenthetical_summary["coverage"] >= 0.9
        )
    )
    saucenao_target_met = (
        saucenao_summary["coverage"] is not None
        and saucenao_summary["coverage"] >= 0.9
        and saucenao_candidate_supply_sufficient
    )
    return {
        "candidate_counts": {
            "total": len(candidates),
            "real_pixiv_high_impact_or_name_like": len(real_pixiv_high),
            "real_pixiv_parenthetical": len(parenthetical),
            "saucenao_real_or_artifact_artist_creator_work": len(saucenao),
        },
        "assertion_status_counts": dict(sorted(Counter(row.status for row in assertions).items())),
        "assertion_role_counts": dict(sorted(Counter(row.asserted_role for row in assertions).items())),
        "real_pixiv_high_impact": real_summary,
        "real_pixiv_candidate_supply_sufficient": real_pixiv_supply_sufficient,
        "real_pixiv_searchable_assertion_target_met": real_pixiv_target_met,
        "real_pixiv_parenthetical": parenthetical_summary,
        "real_pixiv_parenthetical_target_met": parenthetical_target_met,
        "saucenao_real_or_artifact_artist_creator_work": saucenao_summary,
        "saucenao_requested_candidate_target": SAUCENAO_ARTIFACT_RECORD_TARGET,
        "saucenao_available_candidate_supply_target": saucenao_candidate_supply_target,
        "saucenao_candidate_supply_sufficient_for_available_data": saucenao_candidate_supply_sufficient,
        "saucenao_assertion_target_met": saucenao_target_met,
        "fixture_coverage_satisfies_real_targets": False,
        "f5_source_searchable_assertion_goal_met": all(
            [real_pixiv_target_met, parenthetical_target_met, saucenao_target_met]
        ),
    }


def searchable_assertion_search_rows(
    assertions: Sequence[SourceSearchableNameAssertionDraft],
    candidates: Sequence[SearchableNameCandidate],
) -> list[dict[str, Any]]:
    active = [row for row in assertions if row.status == "searchable_active"]
    index: dict[str, list[SourceSearchableNameAssertionDraft]] = defaultdict(list)
    for assertion in active:
        for value in (assertion.raw_input, assertion.normalized_input, assertion.asserted_name, assertion.canonical_name_key):
            key = canonical_source_key(value)
            if key:
                index[key].append(assertion)
    queries: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(label: str, value: Any) -> None:
        text_value = normalize_source_text(value)
        if text_value and text_value not in seen:
            seen.add(text_value)
            queries.append((label, text_value))

    for candidate in candidates:
        if candidate.provider == "pixiv" and candidate.data_type_label == DATA_TYPE_REAL:
            add("real_pixiv_raw_or_normalized", candidate.raw_input)
            add("real_pixiv_normalized", candidate.normalized_input)
            if candidate.parenthetical_outer:
                add("real_pixiv_parenthetical_outer", candidate.parenthetical_outer)
            if candidate.parenthetical_inner:
                add("real_pixiv_parenthetical_work_context", candidate.parenthetical_inner)
            break
    for assertion in active:
        if assertion.provider == "saucenao" and assertion.asserted_role in {"artist", "creator"}:
            add("saucenao_artist_or_creator", assertion.asserted_name or assertion.raw_input)
            break
    for assertion in active:
        if assertion.provider == "saucenao" and assertion.asserted_role == "work_title":
            add("saucenao_work_or_copyright", assertion.asserted_name or assertion.raw_input)
            break
    for assertion in active:
        if len(queries) >= 8:
            break
        add("searchable_active_assertion", assertion.asserted_name or assertion.raw_input)
    add("negative_control", "f5_assertion_no_match_control_query_should_not_match")
    rows = []
    for label, query in queries:
        matched = bool(index.get(canonical_source_key(query)))
        rows.append(
            {
                "query_key": label,
                "query": query,
                "matched": matched,
                "match_count": len(index.get(canonical_source_key(query), [])),
            }
        )
    return rows


def assertion_search_validation_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    positives = [row for row in rows if row.get("query_key") != "negative_control"]
    negative = [row for row in rows if row.get("query_key") == "negative_control"]
    false_positive_suspected = sum(1 for row in negative if row.get("matched"))
    return {
        "query_count": len(rows),
        "matched_count": sum(1 for row in rows if row.get("matched")),
        "unmatched_count": sum(1 for row in rows if not row.get("matched")),
        "positive_query_count": len(positives),
        "positive_matched_count": sum(1 for row in positives if row.get("matched")),
        "false_positive_suspected_count": false_positive_suspected,
        "negative_control_present": bool(negative),
    }


def install_source_registry_write_guard(engine, *, allow_cleanup_deletes: bool = False) -> None:
    write_re = re.compile(r"^\s*(insert|update|delete|alter|drop|truncate|create|merge|replace|copy)\b", re.IGNORECASE)
    destructive_re = re.compile(r"^\s*(delete|drop|truncate|merge|replace|copy|alter|create)\b", re.IGNORECASE)
    allowed_re = re.compile(
        r"^\s*(insert\s+into|update)\s+\"?(?:"
        + "|".join(re.escape(table) for table in sorted(ALLOWED_WRITE_TABLES))
        + r")\"?\b",
        re.IGNORECASE,
    )
    cleanup_delete_re = re.compile(
        r"^\s*delete\s+from\s+\"?(?:"
        + "|".join(re.escape(table) for table in sorted(ALLOWED_WRITE_TABLES))
        + r")\"?\b",
        re.IGNORECASE,
    )

    @event.listens_for(engine, "before_cursor_execute")
    def _guard(_conn, _cursor, statement, _parameters, _context, _executemany):
        sql = str(statement).strip()
        if not write_re.search(sql):
            return
        lowered = sql.casefold()
        if allow_cleanup_deletes and cleanup_delete_re.search(sql):
            return
        if destructive_re.search(sql):
            touched = sorted(table for table in FORBIDDEN_TABLES | ALLOWED_WRITE_TABLES if table in lowered)
            detail = ",".join(touched) if touched else "destructive_write"
            raise f1.ReadOnlyViolation(f"db_write_blocked_f5_destructive:{detail}")
        if allowed_re.search(sql):
            return
        touched_truth_tables = sorted(table for table in FORBIDDEN_TABLES if table in lowered)
        detail = ",".join(touched_truth_tables) if touched_truth_tables else "non_source_registry_write"
        raise f1.ReadOnlyViolation(f"db_write_blocked_except_f5_source_registry_tables:{detail}")


def pr91_merge_confirmation() -> dict[str, Any]:
    head = _git_output(["git", "rev-parse", "HEAD"])
    origin_main = _git_output(["git", "rev-parse", "origin/main"])
    contains = subprocess.run(
        ["git", "merge-base", "--is-ancestor", PR91_MERGE_COMMIT, "HEAD"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    ).returncode == 0
    return {
        "number": 91,
        "state": "MERGED",
        "url": PR91_URL,
        "merge_commit": PR91_MERGE_COMMIT,
        "report_generation_head_sha": head,
        "origin_main_sha_at_report_generation": origin_main,
        "head_contains_pr91_merge_commit": contains,
    }


def _git_output(command: Sequence[str]) -> str | None:
    completed = subprocess.run(
        list(command),
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def schema_summary() -> dict[str, Any]:
    return {
        "additive_migration": "migrate_add_source_metadata_name_registry",
        "tables": sorted(ALLOWED_WRITE_TABLES),
        "forbidden_truth_tables_unchanged": sorted(FORBIDDEN_TABLES),
        "local_source_hint_table_created": False,
        "entity_truth_tables_written": False,
    }


def provider_shapes_summary(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    providers = Counter(canonical_source_key(row.get("provider") or "generic_provider") for row in records)
    data_types = Counter(normalize_source_text(row.get("data_type_label")) or DATA_TYPE_FIXTURE for row in records)
    provider_data_types = Counter(
        f"{canonical_source_key(row.get('provider') or 'generic_provider')}:{normalize_source_text(row.get('data_type_label')) or DATA_TYPE_FIXTURE}"
        for row in records
    )
    shapes = {
        "pixiv": providers.get("pixiv", 0) > 0,
        "saucenao_style": providers.get("saucenao", 0) > 0,
        "danbooru_gelbooru_style": any(provider in providers for provider in ("danbooru", "gelbooru")),
        "google_vision_or_label_style": any(provider in providers for provider in ("google_vision", "generic_label_provider")),
        "no_tag_no_name_provider": providers.get("no_tag_provider", 0) > 0,
    }
    return {
        "provider_counts": dict(sorted(providers.items())),
        "data_type_counts": dict(sorted(data_types.items())),
        "provider_data_type_counts": dict(sorted(provider_data_types.items())),
        "required_shapes": shapes,
        "all_required_shapes_represented": all(shapes.values()),
    }


def search_validation_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "query_count": len(rows),
        "matched_count": sum(1 for row in rows if row.get("matched")),
        "unmatched_count": sum(1 for row in rows if not row.get("matched")),
        "positive_queries_matched": all(
            bool(row.get("matched"))
            for row in rows
            if not str(row.get("query_key", "")).startswith("f5_no_match_control")
        )
        if rows
        else True,
        "no_match_control_present": any(
            str(row.get("query_key", "")).startswith("f5_no_match_control") for row in rows
        ),
    }


def alias_quality_summary(bundle: SourceRegistryBundle) -> dict[str, Any]:
    return {
        "candidate_count": len(bundle.alias_candidates),
        "relation_type_counts": dict(sorted(Counter(row.relation_type for row in bundle.alias_candidates).items())),
        "evidence_source_counts": dict(sorted(Counter(row.evidence_source for row in bundle.alias_candidates).items())),
        "strong_or_curated_count": sum(
            1
            for row in bundle.alias_candidates
            if row.relation_type in {"provider_canonical", "curated_alias"} and (row.confidence or 0) >= 0.85
        ),
        "parenthetical_medium_count": sum(
            1 for row in bundle.alias_candidates if row.relation_type == "parenthetical_character_of_work"
        ),
        "weak_alias_count": sum(1 for row in bundle.alias_candidates if (row.confidence or 0) < 0.7),
        "weak_cooccurrence_affects_canonicalization": False,
    }


def coverage_by_provider_and_data_type(bundle: SourceRegistryBundle) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in bundle.metadata_records:
        grouped[f"{record.provider}:{record.data_type_label}"].append(record)
    names_by_record: dict[str, list[Any]] = defaultdict(list)
    for name in bundle.name_observations:
        names_by_record[provider_record_lookup_key(name.provider, name.provider_record_key)].append(name)
    result: dict[str, Any] = {}
    for key, records in sorted(grouped.items()):
        applicable = [row for row in records if row.signal_roles]
        covered = []
        role_applicable: Counter[str] = Counter()
        role_covered: Counter[str] = Counter()
        raw_flag_counts: Counter[str] = Counter()
        not_applicable_reasons: Counter[str] = Counter()
        for record in records:
            for flag_name, flag_value in record.raw_name_signal_flags.items():
                if flag_value:
                    raw_flag_counts[flag_name] += 1
            if not record.signal_roles:
                not_applicable_reasons[record.no_applicable_name_signal_reason or "no_raw_name_signal"] += 1
        for record in applicable:
            record_key = provider_record_lookup_key(record.provider, record.provider_record_key)
            extracted_roles = {
                name.name_role
                for name in names_by_record.get(record_key, [])
                if name.name_role in {"artist", "creator", "character", "person", "work_title"}
            }
            for role in record.signal_roles:
                role_applicable[role] += 1
                if role in extracted_roles:
                    role_covered[role] += 1
            if set(record.signal_roles).issubset(extracted_roles):
                covered.append(record)
        result[key] = {
            "record_count": len(records),
            "applicable_name_signal_count": len(applicable),
            "covered_name_signal_count": len(covered),
            "not_applicable_no_person_signal_count": len(records) - len(applicable),
            "coverage": round(len(covered) / len(applicable), 4) if applicable else None,
            "role_applicable_counts": dict(sorted(role_applicable.items())),
            "role_covered_counts": dict(sorted(role_covered.items())),
            "role_coverage": {
                role: round(role_covered[role] / role_applicable[role], 4)
                for role in sorted(role_applicable)
                if role_applicable[role]
            },
            "raw_signal_flag_counts": dict(sorted(raw_flag_counts.items())),
            "not_applicable_no_person_signal_reason_counts": dict(sorted(not_applicable_reasons.items())),
        }
    return result


def combined_provider_coverage(
    coverage_by_type: Mapping[str, Mapping[str, Any]],
    *,
    provider: str,
    data_type_labels: Sequence[str],
) -> dict[str, Any]:
    selected = [
        coverage_by_type.get(f"{provider}:{label}", {})
        for label in data_type_labels
    ]
    record_count = sum(int(row.get("record_count") or 0) for row in selected)
    applicable = sum(int(row.get("applicable_name_signal_count") or 0) for row in selected)
    covered = sum(int(row.get("covered_name_signal_count") or 0) for row in selected)
    not_applicable = sum(int(row.get("not_applicable_no_person_signal_count") or 0) for row in selected)
    role_applicable: Counter[str] = Counter()
    role_covered: Counter[str] = Counter()
    for row in selected:
        role_applicable.update(row.get("role_applicable_counts") or {})
        role_covered.update(row.get("role_covered_counts") or {})
    return {
        "provider": provider,
        "data_type_labels": list(data_type_labels),
        "record_count": record_count,
        "applicable_name_signal_count": applicable,
        "covered_name_signal_count": covered,
        "not_applicable_no_person_signal_count": not_applicable,
        "coverage": round(covered / applicable, 4) if applicable else None,
        "role_applicable_counts": dict(sorted(role_applicable.items())),
        "role_covered_counts": dict(sorted(role_covered.items())),
        "role_coverage": {
            role: round(role_covered[role] / role_applicable[role], 4)
            for role in sorted(role_applicable)
            if role_applicable[role]
        },
    }


def no_tag_provider_summary(bundle: SourceRegistryBundle) -> dict[str, Any]:
    records = [row for row in bundle.metadata_records if row.provider == "no_tag_provider"]
    return {
        "record_count": len(records),
        "tag_observation_count": sum(1 for row in bundle.tag_observations if row.provider == "no_tag_provider"),
        "name_observation_count": sum(1 for row in bundle.name_observations if row.provider == "no_tag_provider"),
        "status_counts": dict(sorted(Counter(row.applicability_status for row in records).items())),
        "represented_without_tags_or_names": bool(records)
        and all(row.applicability_status == "not_applicable_no_person_signal" for row in records),
    }


def build_public_summary(
    *,
    run_id: str = "unit-test-run",
    local_artifact_cleanup_summary: Mapping[str, Any] | None = None,
    records: Sequence[Mapping[str, Any]],
    bundle: SourceRegistryBundle,
    searchable_candidates: Sequence[SearchableNameCandidate],
    searchable_name_assertions: Sequence[SourceSearchableNameAssertionDraft],
    llm_classification_summary: Mapping[str, Any],
    input_summary: Mapping[str, Any],
    curated_mapping_count: int,
    db_identity: Mapping[str, Any] | None,
    db_write_summary: Mapping[str, Any],
    search_rows: Sequence[Mapping[str, Any]],
    assertion_search_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    coverage = provider_name_coverage(bundle)
    counts = bundle_public_counts(bundle)
    coverage_by_type = coverage_by_provider_and_data_type(bundle)
    pixiv = coverage["providers"].get("pixiv", {})
    saucenao = coverage["providers"].get("saucenao", {})
    pixiv_real = combined_provider_coverage(coverage_by_type, provider="pixiv", data_type_labels=[DATA_TYPE_REAL])
    saucenao_real_or_artifact = combined_provider_coverage(
        coverage_by_type,
        provider="saucenao",
        data_type_labels=[DATA_TYPE_REAL, DATA_TYPE_ARTIFACT],
    )
    scale_up_summary = dict(input_summary.get("scale_up") or {})
    pixiv_metadata_flow = dict(scale_up_summary.get("real_pixiv_metadata_enrichment_summary") or {})
    real_pixiv_metadata_rich_count = sum(1 for row in records if is_real_pixiv_metadata_rich_record(row))
    reported_enriched_count = int(pixiv_metadata_flow.get("metadata_rich_record_count") or 0)
    real_pixiv_metadata_rich_minimum = int(
        scale_up_summary.get("real_pixiv_metadata_rich_minimum") or REAL_PIXIV_METADATA_RICH_MIN
    )
    real_pixiv_source_prior_summary = dict(scale_up_summary.get("real_pixiv_source_prior_summary") or {})
    real_pixiv_source_prior_min_met = (
        int(real_pixiv_source_prior_summary.get("record_count") or 0)
        >= int(scale_up_summary.get("real_pixiv_source_prior_minimum") or REAL_PIXIV_SOURCE_PRIOR_MIN)
    )
    real_pixiv_sample_min_met = real_pixiv_metadata_rich_count >= real_pixiv_metadata_rich_minimum
    real_pixiv_applicable_min_met = (
        pixiv_real["applicable_name_signal_count"] >= REAL_PIXIV_APPLICABLE_NAME_SIGNAL_TARGET
    )
    real_pixiv_coverage_met = (
        pixiv_real["coverage"] is not None
        and pixiv_real["coverage"] >= 0.8
        and real_pixiv_applicable_min_met
    )
    saucenao_available_record_count = int(
        scale_up_summary.get("existing_saucenao_artifact_record_count")
        or saucenao_real_or_artifact["record_count"]
        or 0
    )
    saucenao_available_record_target = min(SAUCENAO_ARTIFACT_RECORD_TARGET, saucenao_available_record_count)
    saucenao_artifact_sample_min_met = (
        saucenao_real_or_artifact["record_count"] >= saucenao_available_record_target
        and saucenao_available_record_count > 0
    )
    saucenao_non_fixture_coverage_met = (
        saucenao_real_or_artifact["coverage"] is not None
        and saucenao_real_or_artifact["coverage"] >= 0.9
        and saucenao_artifact_sample_min_met
    )
    apply_db_success = bool(db_write_summary.get("apply")) and bool(db_write_summary.get("success"))
    scale_minimum_met = counts["source_metadata_records"] >= MIN_RECORD_COUNT
    hard_max_respected = counts["source_metadata_records"] <= MAX_RECORD_COUNT
    forbidden_delta_zero = db_write_summary.get("forbidden_truth_table_write_count", 0) == 0
    searchable_assertion_coverage = source_searchable_assertion_coverage_summary(
        searchable_candidates,
        searchable_name_assertions,
    )
    searchable_assertion_goal_met = bool(
        searchable_assertion_coverage.get("f5_source_searchable_assertion_goal_met")
    )
    f5_minimum_stage_goal_met = all(
        [
            scale_minimum_met,
            hard_max_respected,
            real_pixiv_source_prior_min_met,
            real_pixiv_sample_min_met,
            real_pixiv_coverage_met,
            saucenao_non_fixture_coverage_met,
            searchable_assertion_goal_met,
            apply_db_success,
            forbidden_delta_zero,
        ]
    )
    current_head_sha = _git_output(["git", "rev-parse", "HEAD"])
    return {
        "phase": PHASE,
        "title": TITLE,
        "run_id": run_id,
        "run_label": "final_closeout_validation",
        "report_generation_head_sha": current_head_sha,
        "public_private_artifacts_from_single_run": True,
        "artifact_lifecycle": "phase_scoped_operational_runner",
        "pr91_merge_confirmation": pr91_merge_confirmation(),
        "local_artifact_cleanup": dict(local_artifact_cleanup_summary or {"performed": False}),
        "input_summary": dict(input_summary),
        "provider_shapes_covered": provider_shapes_summary(records),
        "schema_summary": schema_summary(),
        "row_counts": counts,
        "coverage": coverage,
        "coverage_by_provider_and_data_type": coverage_by_type,
        "expanded_real_data_validation": {
            "real_pixiv": pixiv_real,
            "real_pixiv_metadata_flow": {
                "source_prior_input_count": real_pixiv_source_prior_summary.get("record_count", 0),
                "source_prior_records_at_least_50": real_pixiv_source_prior_min_met,
                "source_prior_only_records_do_not_count_as_metadata_rich": True,
                "metadata_rich_record_count": real_pixiv_metadata_rich_count,
                "reported_enrichment_metadata_rich_record_count": reported_enriched_count,
                "metadata_rich_count_uses_actual_records_not_provider_record_fallback": True,
                "metadata_rich_minimum": real_pixiv_metadata_rich_minimum,
                "metadata_rich_records_at_least_60": real_pixiv_sample_min_met,
                "metadata_acquisition_route": pixiv_metadata_flow.get("metadata_acquisition_route"),
                "field_presence_counts": pixiv_metadata_flow.get("field_presence_counts", {}),
                "gallery_dl_attempted_count": pixiv_metadata_flow.get("gallery_dl_attempted_count", 0),
                "gallery_dl_success_count": pixiv_metadata_flow.get("gallery_dl_success_count", 0),
                "source_prior_only_records_count": pixiv_metadata_flow.get("source_prior_only_records_count", 0),
                "image_downloads": pixiv_metadata_flow.get("image_downloads", 0),
                "image_uploads": pixiv_metadata_flow.get("image_uploads", 0),
            },
            "real_pixiv_source_prior_records_at_least_50": real_pixiv_source_prior_min_met,
            "real_pixiv_metadata_rich_records_at_least_60": real_pixiv_sample_min_met,
            "real_pixiv_applicable_name_signal_target": REAL_PIXIV_APPLICABLE_NAME_SIGNAL_TARGET,
            "real_pixiv_applicable_name_signal_sample_met": real_pixiv_applicable_min_met,
            "real_pixiv_applicable_name_coverage_at_least_80": real_pixiv_coverage_met,
            "saucenao_real_or_artifact": saucenao_real_or_artifact,
            "saucenao_real_or_artifact_requested_record_target": SAUCENAO_ARTIFACT_RECORD_TARGET,
            "saucenao_real_or_artifact_available_record_count": saucenao_available_record_count,
            "saucenao_real_or_artifact_available_record_target": saucenao_available_record_target,
            "saucenao_real_or_artifact_records_meet_available_supply": saucenao_artifact_sample_min_met,
            "saucenao_real_or_artifact_requested_20_available": saucenao_available_record_count >= SAUCENAO_ARTIFACT_RECORD_TARGET,
            "saucenao_real_or_artifact_coverage_at_least_90": saucenao_non_fixture_coverage_met,
            "fixture_coverage_satisfies_real_targets": False,
        },
        "pixiv_applicable_name_coverage": pixiv.get("coverage"),
        "pixiv_coverage_target_met": (pixiv.get("coverage") or 0) >= 0.8,
        "saucenao_applicable_name_coverage": saucenao.get("coverage"),
        "saucenao_coverage_target_met": (saucenao.get("coverage") or 0) >= 0.9,
        "not_applicable_no_person_signal_count": coverage["record_status_counts"].get("not_applicable_no_person_signal", 0),
        "no_tag_provider_result": no_tag_provider_summary(bundle),
        "alias_candidate_quality": alias_quality_summary(bundle),
        "name_search_index_validation": search_validation_summary(search_rows),
        "source_searchable_name_assertion_layer": {
            "classification": dict(llm_classification_summary),
            "coverage": searchable_assertion_coverage,
            "search_validation": assertion_search_validation_summary(assertion_search_rows),
            "assertion_table": "blombooru_source_searchable_name_assertions",
            "entity_truth_created": False,
            "confirmed_assignment_created": False,
        },
        "curated_mapping": {
            "input_mapping_count": curated_mapping_count,
            "template_private_artifact": "curated-name-mapping-template.csv",
            "no_mapping_invented": curated_mapping_count == 0,
        },
        "db_identity": dict(db_identity or {"db_read": False}),
        "db_write_summary": dict(db_write_summary),
        "write_guard_result": {
            "allowed_tables": sorted(ALLOWED_WRITE_TABLES),
            "forbidden_tables": sorted(FORBIDDEN_TABLES),
            "guard_installed_when_apply_db": bool(db_write_summary.get("guard_installed")),
            "forbidden_truth_table_write_count": db_write_summary.get("forbidden_truth_table_write_count", 0),
            "forbidden_table_row_deltas": db_write_summary.get("forbidden_table_row_deltas", {}),
        },
        "f5_minimum_requirements": {
            "source_metadata_record_candidates_at_least_75": scale_minimum_met,
            "source_metadata_record_candidates_preferred_200": counts["source_metadata_records"] >= TARGET_RECORD_COUNT_DEFAULT,
            "source_metadata_record_candidates_hard_max_500": hard_max_respected,
            "real_pixiv_source_prior_records_at_least_50": real_pixiv_source_prior_min_met,
            "real_pixiv_metadata_rich_records_at_least_60": real_pixiv_sample_min_met,
            "real_pixiv_applicable_name_signal_records_at_least_40": real_pixiv_applicable_min_met,
            "apply_db_executed": bool(db_write_summary.get("apply")),
            "apply_db_success": apply_db_success,
            "forbidden_truth_table_write_count_zero": forbidden_delta_zero,
            "pixiv_real_applicable_coverage_at_least_80": real_pixiv_coverage_met,
            "saucenao_real_or_artifact_available_supply_coverage_at_least_90": saucenao_non_fixture_coverage_met,
            "source_searchable_name_assertion_goal_met": searchable_assertion_goal_met,
            "f5_minimum_stage_goal_met": f5_minimum_stage_goal_met,
        },
        "public_report_redaction": {
            "contains_exact_local_paths": False,
            "contains_exact_media_ids": False,
            "contains_exact_pixiv_ids": False,
            "contains_raw_gallery_dl_json": False,
            "contains_raw_image_urls": False,
            "contains_auth_material": False,
            "raw_names_and_aliases_private_only": True,
        },
        "safety_confirmation": {
            "additive_db_migration": True,
            "db_write_limited_to_source_registry_tables": True,
            "entity_write": False,
            "entity_alias_write": False,
            "entity_external_identity_write": False,
            "entity_evidence_write": False,
            "media_entity_candidate_write": False,
            "media_entity_assignment_write": False,
            "local_source_hint_write": False,
            "provider_cache_write": False,
            "negative_lookup_cache_write": False,
            "media_tags_mutation": False,
            "tag_translation_mutation": False,
            "confirmed_assignment": False,
            "llm_classification": bool(llm_classification_summary.get("api_call_attempted")),
            "llm_classification_bounded_unique_candidates": True,
            "provider_upload": False,
            "image_upload": False,
            "broad_provider_run": False,
            "source_or_icloud_mutation": False,
            "app_managed_storage_mutation": False,
            "push_main": False,
            "merge": False,
        },
        "recommendation": {
            "next_route": "review_source_name_registry_then_design_bounded_entity_candidate_bridge",
            "entity_candidate_persistence": "deferred_until_source_name_registry_review",
            "local_source_hint_persistence": "deferred",
            "merge_readiness": "not_ready_for_manual_acceptance_unless_f5_minimum_stage_goal_met"
            if not f5_minimum_stage_goal_met
            else "ready_for_review_if_reviewer_accepts_latest_head",
        },
    }


def build_markdown_report(summary: Mapping[str, Any], *, private_markers: Iterable[str]) -> str:
    lines = [
        f"# {PHASE}: provider-neutral source name registry",
        "",
        "## Summary",
        "",
        f"- Run ID: `{summary['run_id']}`.",
        f"- Report generation head SHA: `{summary['report_generation_head_sha']}`.",
        f"- Local F5 artifact cleanup: `{json.dumps(summary['local_artifact_cleanup'], sort_keys=True)}`.",
        f"- PR #91 merge confirmation: `{json.dumps(summary['pr91_merge_confirmation'], sort_keys=True)}`.",
        f"- Provider shapes covered: `{json.dumps(summary['provider_shapes_covered'], sort_keys=True)}`.",
        f"- Row counts: `{json.dumps(summary['row_counts'], sort_keys=True)}`.",
        f"- F5 minimum requirements: `{json.dumps(summary['f5_minimum_requirements'], sort_keys=True)}`.",
        f"- Pixiv applicable name coverage: `{summary['pixiv_applicable_name_coverage']}`.",
        f"- SauceNAO applicable name coverage: `{summary['saucenao_applicable_name_coverage']}`.",
        f"- Not applicable no-person-signal count: `{summary['not_applicable_no_person_signal_count']}`.",
        f"- Expanded real/artifact validation: `{json.dumps(summary['expanded_real_data_validation'], sort_keys=True)}`.",
        f"- Real Pixiv metadata flow: `{json.dumps(summary['expanded_real_data_validation']['real_pixiv_metadata_flow'], sort_keys=True)}`.",
        f"- Source searchable name assertion layer: `{json.dumps(summary['source_searchable_name_assertion_layer'], sort_keys=True)}`.",
        f"- Reviewer fixes applied: `provider_scoped_metadata_key, registry_merge, numeric_booru_category, saucenao_work_or_copyright, stale_observation_retirement, source_tag_registry_merge, raw_denominator_coverage, complete_write_guard, post_truncation_scale_metrics`.",
        "",
        "## Manual Validation",
        "",
        f"- Open private artifacts under `{_rel(ROOT / PHASE_OUTPUT_DIR)}`; all rows are traceable to run ID `{summary['run_id']}`.",
        "- Start with `manual-review-guide.md`, then inspect `real-pixiv-metadata-rich-records.csv`, `real-pixiv-candidate-inputs.csv`, `source-searchable-name-assertions.csv`, `model-classification-review.csv`, and `name-search-index-validation.csv`.",
        "- Judge whether real Pixiv candidate rows contain provider metadata such as title, artist, tags, page_count/source URL presence, and metadata source; source-prior-only rows must not count as metadata-rich.",
        "- For assertions, review provider, raw_input, asserted_role, status, confidence, reason_code, source_kind/source_field, and requires_review. `searchable_active` is search-facing only and is not Entity truth.",
        "- Check rejected rows with descriptive/generic/not_name_like/popularity_marker reasons and confirm they are not active. Rejected known-name-like reasons should not be counted as coverage.",
        "- Use `name-search-index-validation.csv` to confirm representative source names/assertions are searchable and the negative control does not match.",
        "- Report each sampled row as pass/fail/uncertain with the candidate_key/assertion_key and a short reason.",
        "",
        "## Schema / Storage",
        "",
        f"- Schema summary: `{json.dumps(summary['schema_summary'], sort_keys=True)}`.",
        f"- DB write summary: `{json.dumps(summary['db_write_summary'], sort_keys=True)}`.",
        f"- Write guard: `{json.dumps(summary['write_guard_result'], sort_keys=True)}`.",
        "",
        "## Names / Aliases",
        "",
        f"- Coverage: `{json.dumps(summary['coverage'], sort_keys=True)}`.",
        f"- Coverage by provider/data type: `{json.dumps(summary['coverage_by_provider_and_data_type'], sort_keys=True)}`.",
        f"- Alias candidate quality: `{json.dumps(summary['alias_candidate_quality'], sort_keys=True)}`.",
        f"- Search index validation: `{json.dumps(summary['name_search_index_validation'], sort_keys=True)}`.",
        f"- Assertion search validation: `{json.dumps(summary['source_searchable_name_assertion_layer']['search_validation'], sort_keys=True)}`.",
        f"- No-tag provider result: `{json.dumps(summary['no_tag_provider_result'], sort_keys=True)}`.",
        f"- Curated mapping: `{json.dumps(summary['curated_mapping'], sort_keys=True)}`.",
        "",
        "## Safety Confirmation",
        "",
        f"- Safety: `{json.dumps(summary['safety_confirmation'], sort_keys=True)}`.",
        f"- Redaction: `{json.dumps(summary['public_report_redaction'], sort_keys=True)}`.",
        "",
        "## Recommendation",
        "",
        f"- Recommendation: `{json.dumps(summary['recommendation'], sort_keys=True)}`.",
        "",
    ]
    report = "\n".join(lines)
    f1.assert_public_payload_safe(report, private_markers=private_markers)
    return report


def curated_mapping_template_csv() -> str:
    rows = [
        {
            "source_name": "",
            "target_name": "",
            "relation_type": "curated_alias",
            "name_role": "character",
            "candidate_namespace": "source_name",
            "confidence": "0.95",
            "source": "operator_curated",
            "notes": "",
        }
    ]
    return _csv(rows)


def manual_review_guide(run_id: str) -> str:
    return "\n".join(
        [
            "# Phase 4.4-P2R-F5 manual review guide",
            "",
            f"Run ID: `{run_id}`",
            "",
            "## Files to open first",
            "",
            f"1. `{_rel(ROOT / PHASE_OUTPUT_DIR / 'real-pixiv-metadata-rich-records.csv')}`",
            f"2. `{_rel(ROOT / PHASE_OUTPUT_DIR / 'real-pixiv-candidate-inputs.csv')}`",
            f"3. `{_rel(ROOT / PHASE_OUTPUT_DIR / 'source-searchable-name-assertions.csv')}`",
            f"4. `{_rel(ROOT / PHASE_OUTPUT_DIR / 'model-classification-review.csv')}`",
            f"5. `{_rel(ROOT / PHASE_OUTPUT_DIR / 'name-search-index-validation.csv')}`",
            f"6. `{_rel(ROOT / PHASE_OUTPUT_DIR / 'source-metadata-evidence.csv')}` for source observation evidence.",
            f"7. `{_rel(ROOT / PHASE_OUTPUT_DIR / 'details.json')}` if a CSV row needs full private context.",
            "",
            "## What to inspect",
            "",
            "- Confirm sampled `real-pixiv-metadata-rich-records.csv` rows have real provider metadata: title, artist/user, tags, page_count/source URL presence, and metadata source/provenance.",
            "- Confirm source-prior-only rows are not counted as metadata-rich; rows in this file should have `metadata_kind=gallery_dl_real_pixiv_metadata` or equivalent provider metadata.",
            "- In `real-pixiv-candidate-inputs.csv`, inspect `raw_input`, `source_kind`, `source_field`, `role_hint`, and context fields such as title, artist_name, sibling_tags, metadata_source, and run_id.",
            "- In `source-searchable-name-assertions.csv`, inspect `status`, `asserted_role`, `asserted_name`, `confidence`, `requires_review`, `evidence_sources_json`, and `provenance_summary`.",
            "- `searchable_active` means source-search usable only. It is not Entity truth, not EntityAlias, not a MediaEntityCandidate, and not a confirmed assignment.",
            "- Rejected rows with `descriptive_tag`, `generic_label`, `not_name_like`, or `popularity_marker` should remain non-active. Rejected known name-like rows should not be treated as covered.",
            "- Use `model-classification-inputs.jsonl` and `model-classification-outputs.jsonl` to compare each LLM input chunk with the validated review row if a classification looks surprising.",
            "- Use `name-search-index-validation.csv` to verify representative positive queries match and the negative control does not match.",
            "",
            "## Suggested sampling",
            "",
            "- Sample at least 10 real Pixiv `searchable_active` assertions across tags, source_name_observation rows, and source_title candidates.",
            "- Sample at least 5 rejected/general/popularity rows and confirm none is active.",
            "- Sample at least 5 SauceNAO provider-field assertions, especially `work_or_copyright` rows.",
            "- Sample at least 5 alias candidates and confirm provider/parenthetical/curated evidence is visible and reviewable.",
            "",
            "## How to report results",
            "",
            "- Mark each sampled row as `pass`, `fail`, or `uncertain`.",
            "- Include the `candidate_key` or `assertion_key`, provider, raw input, status, and a short reason.",
            "- Treat uncertain source-search assertions as a normal manual-review signal, not as confirmed truth.",
            "",
            "## Boundaries",
            "",
            "- SourceNameRegistry rows are searchable source observations, not Entities.",
            "- SourceSearchableNameAssertion rows are source-search assertions, not Entity truth or confirmed assignments.",
            "- SourceNameAliasCandidate rows are candidate relations, not EntityAlias truth.",
            "- Strong aliases need provider canonical, curated, or trusted local/external provenance.",
            "- Parenthetical relations are useful medium evidence and should remain reviewable.",
            "- Co-occurrence, if added later, must stay weak and must not canonicalize search by itself.",
            "- No-tag/no-name providers should keep SourceMetadataRecord rows with zero tag/name observations.",
            "",
        ]
    )


def private_markers(
    bundle: SourceRegistryBundle,
    records: Sequence[Mapping[str, Any]],
    searchable_name_assertions: Sequence[SourceSearchableNameAssertionDraft] = (),
) -> list[str]:
    markers: set[str] = set()
    for record in records:
        for key in (
            "artist_name",
            "artist",
            "creator",
            "author",
            "title",
            "work",
            "material",
            "copyright",
            "work_or_copyright",
            "characters",
            "character",
            "person",
        ):
            value = record.get(key)
            if isinstance(value, str) and value:
                markers.add(value)
            elif isinstance(value, (list, tuple, set)):
                markers.update(str(item) for item in value if item)
        for key in ("source_url", "post_url", "source_work_id", "work_id", "provider_record_key"):
            value = record.get(key)
            if isinstance(value, str) and value:
                markers.add(value)
    for row in bundle.name_observations:
        markers.add(row.raw_name)
    for row in bundle.tag_observations:
        markers.add(row.raw_tag)
    for row in bundle.alias_candidates:
        markers.add(row.source_display_name)
        markers.add(row.target_display_name)
    for row in searchable_name_assertions:
        markers.add(row.raw_input)
        markers.add(row.asserted_name or "")
        markers.add(row.reasoning_summary_private or "")
    return sorted({marker for marker in markers if marker and len(marker) > 2}, key=len, reverse=True)


def search_validation_queries(bundle: SourceRegistryBundle) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        text_value = normalize_source_text(value)
        if text_value and text_value not in seen:
            queries.append(text_value)
            seen.add(text_value)

    def add_first(predicate) -> None:
        for name in bundle.name_observations:
            if predicate(name):
                add(name.raw_name)
                return

    add_first(lambda name: name.provider == "pixiv" and name.source_field == "pixiv_parenthetical_outer")
    add_first(lambda name: name.provider == "pixiv" and name.source_field == "pixiv_parenthetical_inner_work")
    add_first(lambda name: name.provider == "saucenao" and name.source_field in {"saucenao_artist", "saucenao_creator"})
    add_first(lambda name: name.provider == "saucenao" and name.source_field == "saucenao_work_or_copyright")
    add_first(lambda name: name.provider in {"danbooru", "gelbooru"} and name.name_role == "character")
    for alias in bundle.alias_candidates:
        if alias.relation_type == "provider_canonical":
            add(alias.source_display_name)
            break
    for name in bundle.name_observations:
        if len(queries) >= 8:
            break
        add(name.raw_name)
    queries.append("f5_no_match_control_query_should_not_match")
    return queries


def table_row_counts(session, table_names: Iterable[str]) -> dict[str, int | None]:
    existing_tables = set(inspect(session.bind).get_table_names())
    counts: dict[str, int | None] = {}
    for table_name in sorted(table_names):
        if table_name not in existing_tables:
            counts[table_name] = None
            continue
        counts[table_name] = int(session.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar() or 0)
    return counts


def table_row_deltas(before: Mapping[str, int | None], after: Mapping[str, int | None]) -> dict[str, int]:
    deltas: dict[str, int] = {}
    for table_name in sorted(set(before) | set(after)):
        before_count = before.get(table_name)
        after_count = after.get(table_name)
        if before_count is None or after_count is None:
            deltas[table_name] = 0
        else:
            deltas[table_name] = int(after_count) - int(before_count)
    return deltas


def cleanup_f5_source_tables(session) -> dict[str, Any]:
    existing_tables = set(inspect(session.bind).get_table_names())
    before = table_row_counts(session, ALLOWED_WRITE_TABLES)
    deleted: dict[str, int] = {}
    skipped: list[str] = []
    for table_name in F5_CLEANUP_TABLE_DELETE_ORDER:
        if table_name not in existing_tables:
            skipped.append(table_name)
            continue
        result = session.execute(text(f'DELETE FROM "{table_name}"'))
        deleted[table_name] = int(result.rowcount or 0)
    session.commit()
    after = table_row_counts(session, ALLOWED_WRITE_TABLES)
    return {
        "performed": True,
        "delete_order": list(F5_CLEANUP_TABLE_DELETE_ORDER),
        "deleted_rows": deleted,
        "skipped_missing_tables": skipped,
        "allowed_table_row_counts_before_cleanup": before,
        "allowed_table_row_counts_after_cleanup": after,
        "allowed_table_row_deltas_cleanup": table_row_deltas(before, after),
        "total_deleted_rows": sum(deleted.values()),
    }


def db_apply_summary(
    args: argparse.Namespace,
    bundle: SourceRegistryBundle,
    searchable_name_assertions: Sequence[SourceSearchableNameAssertionDraft],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if args.no_db or args.dry_run or not args.apply_db:
        planned = bundle_public_counts(bundle)
        planned["source_searchable_name_assertions"] = len(searchable_name_assertions)
        return None, {
            "apply": False,
            "reason": "no_db_or_dry_run_or_apply_db_not_set",
            "guard_installed": False,
            "planned": planned,
            "forbidden_truth_table_write_count": 0,
        }

    config = load_f5_project_config()
    engine = create_engine(config.database_url)
    migrate_add_source_metadata_name_registry(engine, inspect(engine))
    install_source_registry_write_guard(engine, allow_cleanup_deletes=bool(getattr(args, "clean_f5_state", False)))
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        db_identity = f1.prove_db_identity(session, config)
        forbidden_before = table_row_counts(session, FORBIDDEN_TABLES)
        allowed_pre_cleanup = table_row_counts(session, ALLOWED_WRITE_TABLES)
        cleanup_summary: dict[str, Any] = {"performed": False}
        if bool(getattr(args, "clean_f5_state", False)):
            cleanup_summary = cleanup_f5_source_tables(session)
        allowed_before = table_row_counts(session, ALLOWED_WRITE_TABLES)
        write_summary = persist_source_registry_bundle(
            session,
            bundle,
            apply=True,
            searchable_name_assertions=searchable_name_assertions,
        )
        forbidden_after = table_row_counts(session, FORBIDDEN_TABLES)
        allowed_after = table_row_counts(session, ALLOWED_WRITE_TABLES)
        forbidden_deltas = table_row_deltas(forbidden_before, forbidden_after)
        forbidden_delta_total = sum(abs(value) for value in forbidden_deltas.values())
        write_summary["cleanup_summary"] = cleanup_summary
        write_summary["allowed_table_row_counts_pre_cleanup"] = allowed_pre_cleanup
        write_summary["allowed_table_row_counts_before"] = allowed_before
        write_summary["allowed_table_row_counts_after"] = allowed_after
        write_summary["allowed_table_row_deltas"] = table_row_deltas(allowed_before, allowed_after)
        write_summary["allowed_table_row_deltas_from_pre_cleanup"] = table_row_deltas(allowed_pre_cleanup, allowed_after)
        write_summary["forbidden_table_row_counts_before"] = forbidden_before
        write_summary["forbidden_table_row_counts_after"] = forbidden_after
        write_summary["forbidden_table_row_deltas"] = forbidden_deltas
        write_summary["forbidden_truth_table_write_count"] = forbidden_delta_total
        write_summary["guard_installed"] = True
        if forbidden_delta_total:
            raise Phase44P2RF5Error("forbidden_truth_table_row_count_changed")
        return db_identity, write_summary
    finally:
        session.close()
        engine.dispose()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-json", default="")
    parser.add_argument("--curated-mapping-input", default="")
    parser.add_argument("--output-dir", default=str(PHASE_OUTPUT_DIR))
    parser.add_argument("--report-md", default=str(REPORT_MD))
    parser.add_argument("--report-json", default=str(REPORT_JSON))
    parser.add_argument("--details-json", default=str(PRIVATE_DETAILS_JSON))
    parser.add_argument("--source-metadata-csv", default=str(PRIVATE_SOURCE_METADATA_CSV))
    parser.add_argument("--source-tag-observations-csv", default=str(PRIVATE_SOURCE_TAG_OBSERVATIONS_CSV))
    parser.add_argument("--source-tag-registry-csv", default=str(PRIVATE_SOURCE_TAG_REGISTRY_CSV))
    parser.add_argument("--source-name-observations-csv", default=str(PRIVATE_SOURCE_NAME_OBSERVATIONS_CSV))
    parser.add_argument("--source-name-registry-csv", default=str(PRIVATE_SOURCE_NAME_REGISTRY_CSV))
    parser.add_argument("--source-name-alias-candidates-csv", default=str(PRIVATE_SOURCE_NAME_ALIAS_CANDIDATES_CSV))
    parser.add_argument("--source-metadata-evidence-csv", default=str(PRIVATE_SOURCE_METADATA_EVIDENCE_CSV))
    parser.add_argument("--source-searchable-name-assertions-csv", default=str(PRIVATE_SOURCE_SEARCHABLE_NAME_ASSERTIONS_CSV))
    parser.add_argument("--model-classification-inputs-jsonl", default=str(PRIVATE_MODEL_CLASSIFICATION_INPUTS_JSONL))
    parser.add_argument("--model-classification-outputs-jsonl", default=str(PRIVATE_MODEL_CLASSIFICATION_OUTPUTS_JSONL))
    parser.add_argument("--model-classification-review-csv", default=str(PRIVATE_MODEL_CLASSIFICATION_REVIEW_CSV))
    parser.add_argument("--real-pixiv-searchable-candidates-csv", default=str(PRIVATE_REAL_PIXIV_SEARCHABLE_CANDIDATES_CSV))
    parser.add_argument("--real-pixiv-candidate-inputs-csv", default=str(PRIVATE_REAL_PIXIV_CANDIDATE_INPUTS_CSV))
    parser.add_argument("--real-pixiv-metadata-rich-records-csv", default=str(PRIVATE_REAL_PIXIV_METADATA_RICH_RECORDS_CSV))
    parser.add_argument("--provider-name-coverage-csv", default=str(PRIVATE_PROVIDER_NAME_COVERAGE_CSV))
    parser.add_argument("--raw-applicable-name-signals-csv", default=str(PRIVATE_RAW_APPLICABLE_NAME_SIGNALS_CSV))
    parser.add_argument("--search-validation-csv", default=str(PRIVATE_SEARCH_VALIDATION_CSV))
    parser.add_argument("--curated-template-csv", default=str(PRIVATE_CURATED_TEMPLATE_CSV))
    parser.add_argument("--manual-review-guide", default=str(PRIVATE_MANUAL_REVIEW_GUIDE))
    parser.add_argument("--apply-db", action="store_true")
    parser.add_argument("--no-db", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--clean-local-artifacts", action="store_true")
    parser.add_argument("--clean-f5-state", action="store_true")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--run-label", default="final_closeout_validation")
    parser.add_argument("--target-record-count", type=int, default=TARGET_RECORD_COUNT_DEFAULT)
    parser.add_argument("--real-pixiv-source-prior-min", type=int, default=REAL_PIXIV_SOURCE_PRIOR_MIN)
    parser.add_argument("--real-pixiv-metadata-rich-min", type=int, default=REAL_PIXIV_METADATA_RICH_MIN)
    parser.add_argument("--gallery-dl-metadata-attempt-limit", type=int, default=REAL_PIXIV_GALLERY_DL_ATTEMPT_LIMIT_DEFAULT)
    parser.add_argument("--gallery-dl-command", default=os.getenv("VIOLET_GALLERY_DL_COMMAND", ""))
    parser.add_argument("--gallery-dl-metadata-raw-dir", default=str(PRIVATE_GALLERY_DL_REAL_PIXIV_METADATA_DIR))
    parser.add_argument("--disable-gallery-dl-metadata-enrichment", action="store_true")
    parser.add_argument("--max-records", type=int, default=MAX_RECORD_COUNT)
    parser.add_argument("--disable-scale-up", action="store_true")
    parser.add_argument("--use-llm-api", action="store_true")
    parser.add_argument("--llm-preflight-only", action="store_true")
    parser.add_argument("--source-assertion-max-candidates", type=int, default=SOURCE_ASSERTION_DEFAULT_MAX_CANDIDATES)
    parser.add_argument("--source-assertion-chunk-size", type=int, default=5)
    parser.add_argument("--source-assertion-max-tokens", type=int, default=6000)
    parser.add_argument("--source-assertion-api-retries", type=int, default=1)
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.llm_preflight_only:
        return source_assertion_llm_preflight(args)

    output_dir = resolve_repo_path(args.output_dir)
    private_paths = [
        resolve_repo_path(args.details_json),
        resolve_repo_path(args.source_metadata_csv),
        resolve_repo_path(args.source_tag_observations_csv),
        resolve_repo_path(args.source_tag_registry_csv),
        resolve_repo_path(args.source_name_observations_csv),
        resolve_repo_path(args.source_name_registry_csv),
        resolve_repo_path(args.source_name_alias_candidates_csv),
        resolve_repo_path(args.source_metadata_evidence_csv),
        resolve_repo_path(args.source_searchable_name_assertions_csv),
        resolve_repo_path(args.model_classification_inputs_jsonl),
        resolve_repo_path(args.model_classification_outputs_jsonl),
        resolve_repo_path(args.model_classification_review_csv),
        resolve_repo_path(args.real_pixiv_searchable_candidates_csv),
        resolve_repo_path(args.real_pixiv_candidate_inputs_csv),
        resolve_repo_path(args.real_pixiv_metadata_rich_records_csv),
        resolve_repo_path(args.provider_name_coverage_csv),
        resolve_repo_path(args.raw_applicable_name_signals_csv),
        resolve_repo_path(args.search_validation_csv),
        resolve_repo_path(args.curated_template_csv),
        resolve_repo_path(args.manual_review_guide),
    ]
    validate_all_output_paths_before_effects(
        output_dir,
        private_paths=private_paths,
        report_json=resolve_repo_path(args.report_json),
        report_md=resolve_repo_path(args.report_md),
    )
    require_under_phase_output(resolve_repo_path(args.gallery_dl_metadata_raw_dir))
    if int(args.source_assertion_max_candidates) > SOURCE_ASSERTION_HARD_MAX_CANDIDATES:
        raise Phase44P2RF5Error("source_assertion_candidate_hard_max_exceeded")
    if int(args.source_assertion_chunk_size) <= 0:
        raise Phase44P2RF5Error("source_assertion_chunk_size_invalid")
    if int(args.source_assertion_max_tokens) <= 0:
        raise Phase44P2RF5Error("source_assertion_max_tokens_invalid")
    if int(args.source_assertion_api_retries) < 0:
        raise Phase44P2RF5Error("source_assertion_api_retries_invalid")

    run_id = normalize_source_text(args.run_id) or generate_run_id()
    run_label = normalize_source_text(args.run_label) or "final_closeout_validation"
    local_artifact_cleanup_summary: dict[str, Any] = {"performed": False}
    if bool(args.clean_local_artifacts):
        local_artifact_cleanup_summary = cleanup_phase_output_dir(output_dir)

    records, input_summary = load_provider_records(args.input_json or None)
    records, scale_summary = scale_provider_records(records, args)
    records = attach_final_run_metadata(records, run_id=run_id, run_label=run_label)
    input_summary = {**dict(input_summary), "scale_up": scale_summary}
    input_summary["run_id"] = run_id
    input_summary["run_label"] = run_label
    input_summary["local_artifact_cleanup"] = local_artifact_cleanup_summary
    curated_mappings = load_curated_mappings(args.curated_mapping_input or None)
    bundle = build_source_registry_bundle(records, curated_mappings=curated_mappings)
    search_rows = validate_search_queries(bundle, search_validation_queries(bundle))
    (
        searchable_candidates,
        searchable_name_assertions,
        llm_classification_summary,
        model_inputs_private,
        model_outputs_private,
        model_review_rows,
    ) = classify_source_searchable_name_assertions(args, bundle, records)
    assertion_search_rows = searchable_assertion_search_rows(searchable_name_assertions, searchable_candidates)
    db_identity, db_write_summary = db_apply_summary(args, bundle, searchable_name_assertions)
    real_pixiv_metadata_rich_records = [row for row in records if is_real_pixiv_metadata_rich_record(row)]
    real_pixiv_candidate_input_rows = [
        asdict(row)
        for row in searchable_candidates
        if row.provider == "pixiv" and row.data_type_label == DATA_TYPE_REAL
    ]
    summary = build_public_summary(
        run_id=run_id,
        local_artifact_cleanup_summary=local_artifact_cleanup_summary,
        records=records,
        bundle=bundle,
        searchable_candidates=searchable_candidates,
        searchable_name_assertions=searchable_name_assertions,
        llm_classification_summary=llm_classification_summary,
        input_summary=input_summary,
        curated_mapping_count=len(curated_mappings),
        db_identity=db_identity,
        db_write_summary=db_write_summary,
        search_rows=search_rows,
        assertion_search_rows=assertion_search_rows,
    )
    markers = private_markers(bundle, records, searchable_name_assertions)
    f1.assert_public_payload_safe(summary, private_markers=markers)
    report = build_markdown_report(summary, private_markers=markers)

    write_public_json(resolve_repo_path(args.report_json), summary)
    write_public_text(resolve_repo_path(args.report_md), report, private_markers=markers)
    write_private_json(
        resolve_repo_path(args.details_json),
        {
            "run_id": run_id,
            "run_label": run_label,
            "private_exact_names_and_tags": True,
            "records_private": list(records),
            "source_metadata_records": [asdict(row) for row in bundle.metadata_records],
            "source_tag_observations": [asdict(row) for row in bundle.tag_observations],
            "source_tag_registry": [asdict(row) for row in bundle.tag_registry],
            "source_name_observations": [asdict(row) for row in bundle.name_observations],
            "source_name_registry": [asdict(row) for row in bundle.name_registry],
            "source_name_alias_candidates": [asdict(row) for row in bundle.alias_candidates],
            "source_metadata_evidence": [asdict(row) for row in bundle.evidence],
            "source_searchable_name_candidates": [asdict(row) for row in searchable_candidates],
            "real_pixiv_metadata_rich_records": real_pixiv_metadata_rich_records,
            "real_pixiv_candidate_inputs": real_pixiv_candidate_input_rows,
            "source_searchable_name_assertions": [asdict(row) for row in searchable_name_assertions],
            "model_classification_review": model_review_rows,
            "raw_applicable_name_signals": raw_applicable_signal_rows(bundle),
            "name_search_index": build_name_search_index(bundle),
            "source_searchable_assertion_search_validation": assertion_search_rows,
            "summary_public": summary,
        },
    )
    write_private_text(resolve_repo_path(args.source_metadata_csv), _csv(asdict(row) for row in bundle.metadata_records))
    write_private_text(resolve_repo_path(args.source_tag_observations_csv), _csv(asdict(row) for row in bundle.tag_observations))
    write_private_text(resolve_repo_path(args.source_tag_registry_csv), _csv(asdict(row) for row in bundle.tag_registry))
    write_private_text(resolve_repo_path(args.source_name_observations_csv), _csv(asdict(row) for row in bundle.name_observations))
    write_private_text(resolve_repo_path(args.source_name_registry_csv), _csv(asdict(row) for row in bundle.name_registry))
    write_private_text(resolve_repo_path(args.source_name_alias_candidates_csv), _csv(asdict(row) for row in bundle.alias_candidates))
    write_private_text(resolve_repo_path(args.source_metadata_evidence_csv), _csv(asdict(row) for row in bundle.evidence))
    write_private_text(resolve_repo_path(args.source_searchable_name_assertions_csv), _csv(asdict(row) for row in searchable_name_assertions))
    write_private_text(resolve_repo_path(args.model_classification_inputs_jsonl), _jsonl(model_inputs_private))
    write_private_text(resolve_repo_path(args.model_classification_outputs_jsonl), _jsonl(model_outputs_private))
    write_private_text(resolve_repo_path(args.model_classification_review_csv), _csv(model_review_rows))
    write_private_text(resolve_repo_path(args.real_pixiv_searchable_candidates_csv), _csv(real_pixiv_candidate_input_rows))
    write_private_text(resolve_repo_path(args.real_pixiv_candidate_inputs_csv), _csv(real_pixiv_candidate_input_rows))
    write_private_text(resolve_repo_path(args.real_pixiv_metadata_rich_records_csv), _csv(real_pixiv_metadata_rich_records))
    write_private_text(resolve_repo_path(args.provider_name_coverage_csv), _coverage_csv(provider_name_coverage(bundle)))
    write_private_text(resolve_repo_path(args.raw_applicable_name_signals_csv), _csv(raw_applicable_signal_rows(bundle)))
    write_private_text(resolve_repo_path(args.search_validation_csv), _csv(list(search_rows) + list(assertion_search_rows)))
    write_private_text(resolve_repo_path(args.curated_template_csv), curated_mapping_template_csv())
    write_private_text(resolve_repo_path(args.manual_review_guide), manual_review_guide(run_id))

    return {
        "run_id": run_id,
        "report_md": _rel(resolve_repo_path(args.report_md)),
        "report_json": _rel(resolve_repo_path(args.report_json)),
        "source_metadata_records": len(bundle.metadata_records),
        "source_name_registry": len(bundle.name_registry),
        "alias_candidates": len(bundle.alias_candidates),
        "source_searchable_name_assertions": len(searchable_name_assertions),
        "real_pixiv_metadata_rich_records": len(real_pixiv_metadata_rich_records),
        "real_pixiv_candidate_inputs": len(real_pixiv_candidate_input_rows),
        "source_assertion_mode": llm_classification_summary.get("mode"),
        "pixiv_coverage": summary["pixiv_applicable_name_coverage"],
        "saucenao_coverage": summary["saucenao_applicable_name_coverage"],
    }


def _csv(rows_iter: Iterable[Mapping[str, Any]]) -> str:
    rows = [dict(row) for row in rows_iter]
    if not rows:
        return ""
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _cell(row.get(key)) for key in fieldnames})
    return buffer.getvalue()


def _jsonl(rows_iter: Iterable[Mapping[str, Any]]) -> str:
    return "".join(
        json.dumps(f1._coerce_json_safe(dict(row)), ensure_ascii=False, sort_keys=True, default=str) + "\n"
        for row in rows_iter
    )


def _cell(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(f1._coerce_json_safe(value), ensure_ascii=False, sort_keys=True)
    return value


def _coverage_csv(payload: Mapping[str, Any]) -> str:
    rows = []
    for provider, summary in payload.get("providers", {}).items():
        row = {"provider": provider, **summary}
        rows.append(row)
    return _csv(rows)


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = run(args)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
