"""Phase 4.4-P2R-F5 provider-neutral source name registry foundation.

Lifecycle: phase-scoped operational runner plus additive source-layer storage
foundation. It writes only the F5 source metadata/tag/name/alias/evidence
staging tables when `--apply-db` is explicitly used.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

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
from app.services.source_metadata_registry_service import (  # noqa: E402
    CuratedNameMapping,
    SourceRegistryBundle,
    build_name_search_index,
    build_source_registry_bundle,
    bundle_public_counts,
    canonical_source_key,
    normalize_source_text,
    persist_source_registry_bundle,
    provider_name_coverage,
    validate_search_queries,
)
from scripts import run_phase44p2r_f1_gallery_dl_json_import_pilot as f1  # noqa: E402

PHASE = "4.4-P2R-F5"
PHASE_SLUG = "phase-4.4p2r-f5-provider-neutral-source-name-registry"
TITLE = "Provider-Neutral Source Metadata + Source Name / Alias Registry Foundation"
DATA_TYPE_REAL = "real_live_or_local_provider_data"
DATA_TYPE_ARTIFACT = "existing_artifact_or_report_derived"
DATA_TYPE_FIXTURE = "fixture_or_mock"
DATA_TYPE_LABELS = {DATA_TYPE_REAL, DATA_TYPE_ARTIFACT, DATA_TYPE_FIXTURE}
TARGET_RECORD_COUNT_DEFAULT = 75
MIN_RECORD_COUNT = 50
REAL_PIXIV_SOURCE_PRIOR_MIN = 30
MAX_RECORD_COUNT = 100

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
PRIVATE_PROVIDER_NAME_COVERAGE_CSV = PHASE_OUTPUT_DIR / "provider-name-coverage.csv"
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


class Phase44P2RF5Error(RuntimeError):
    pass


class OutputPathError(Phase44P2RF5Error):
    pass


class CuratedMappingError(Phase44P2RF5Error):
    pass


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

    def add_many(rows: Iterable[Mapping[str, Any]]) -> int:
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
        return added

    real_pixiv_records: list[dict[str, Any]] = []
    real_pixiv_summary: dict[str, Any] = {"attempted": False, "record_count": 0}
    if not args.no_db and not args.dry_run:
        real_pixiv_records, real_pixiv_summary = load_real_pixiv_source_prior_records_from_db(
            limit=max(int(args.real_pixiv_source_prior_min), REAL_PIXIV_SOURCE_PRIOR_MIN)
        )
    add_many(real_pixiv_records)

    artifact_saucenao_records = load_existing_saucenao_artifact_records(limit=10)
    add_many(artifact_saucenao_records)
    add_many(records)
    add_many(generated_scale_fixture_records(start_index=1, count=target_count - len(result)))

    data_type_counts = Counter(row.get("data_type_label") for row in result)
    return result[: int(args.max_records)], {
        "scale_up_enabled": True,
        "target_record_count": target_count,
        "actual_record_count": len(result[: int(args.max_records)]),
        "minimum_record_count_required": MIN_RECORD_COUNT,
        "preferred_record_count": TARGET_RECORD_COUNT_DEFAULT,
        "max_record_count": int(args.max_records),
        "data_type_counts": dict(sorted(data_type_counts.items())),
        "real_pixiv_source_prior_minimum": int(args.real_pixiv_source_prior_min),
        "real_pixiv_source_prior_summary": real_pixiv_summary,
        "existing_saucenao_artifact_record_count": len(artifact_saucenao_records),
        "fixture_or_mock_record_count": data_type_counts.get(DATA_TYPE_FIXTURE, 0),
        "meets_scale_minimum": len(result) >= MIN_RECORD_COUNT,
        "meets_real_pixiv_source_prior_minimum": len(real_pixiv_records) >= int(args.real_pixiv_source_prior_min),
    }


def load_real_pixiv_source_prior_records_from_db(*, limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    config = f1.load_project_config(ROOT)
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


def load_existing_saucenao_artifact_records(*, limit: int) -> list[dict[str, Any]]:
    path = ROOT / "docs/reports/phase-4.4b1-manual-validation-and-saucenao-metadata-audit-summary.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = (
        payload.get("metadata_extraction_audit", {}).get("items", [])
        if isinstance(payload, Mapping)
        else []
    )
    records: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
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
        if len(records) >= limit:
            break
    return _ensure_data_type_labels(records, default_label=DATA_TYPE_ARTIFACT)


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


def install_source_registry_write_guard(engine) -> None:
    write_re = re.compile(r"^\s*(insert|update|delete|alter|drop|truncate|create)\b", re.IGNORECASE)
    destructive_re = re.compile(r"^\s*(delete|drop|truncate)\b", re.IGNORECASE)
    allowed_re = re.compile(
        r"^\s*(insert\s+into|update)\s+\"?(?:"
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
        names_by_record[f"{name.provider}::{name.provider_record_key}"].append(name)
    result: dict[str, Any] = {}
    for key, records in sorted(grouped.items()):
        applicable = [row for row in records if row.applicability_status != "not_applicable_no_person_signal"]
        covered = [row for row in applicable if names_by_record.get(f"{row.provider}::{row.provider_record_key}")]
        result[key] = {
            "record_count": len(records),
            "applicable_name_signal_count": len(applicable),
            "covered_name_signal_count": len(covered),
            "not_applicable_no_person_signal_count": len(records) - len(applicable),
            "coverage": round(len(covered) / len(applicable), 4) if applicable else None,
        }
    return result


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
    records: Sequence[Mapping[str, Any]],
    bundle: SourceRegistryBundle,
    input_summary: Mapping[str, Any],
    curated_mapping_count: int,
    db_identity: Mapping[str, Any] | None,
    db_write_summary: Mapping[str, Any],
    search_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    coverage = provider_name_coverage(bundle)
    counts = bundle_public_counts(bundle)
    pixiv = coverage["providers"].get("pixiv", {})
    saucenao = coverage["providers"].get("saucenao", {})
    return {
        "phase": PHASE,
        "title": TITLE,
        "artifact_lifecycle": "phase_scoped_operational_runner",
        "pr91_merge_confirmation": pr91_merge_confirmation(),
        "input_summary": dict(input_summary),
        "provider_shapes_covered": provider_shapes_summary(records),
        "schema_summary": schema_summary(),
        "row_counts": counts,
        "coverage": coverage,
        "coverage_by_provider_and_data_type": coverage_by_provider_and_data_type(bundle),
        "pixiv_applicable_name_coverage": pixiv.get("coverage"),
        "pixiv_coverage_target_met": (pixiv.get("coverage") or 0) >= 0.8,
        "saucenao_applicable_name_coverage": saucenao.get("coverage"),
        "saucenao_coverage_target_met": (saucenao.get("coverage") or 0) >= 0.9,
        "not_applicable_no_person_signal_count": coverage["record_status_counts"].get("not_applicable_no_person_signal", 0),
        "no_tag_provider_result": no_tag_provider_summary(bundle),
        "alias_candidate_quality": alias_quality_summary(bundle),
        "name_search_index_validation": search_validation_summary(search_rows),
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
            "source_metadata_record_candidates_at_least_50": counts["source_metadata_records"] >= MIN_RECORD_COUNT,
            "source_metadata_record_candidates_preferred_75": counts["source_metadata_records"] >= TARGET_RECORD_COUNT_DEFAULT,
            "source_metadata_record_candidates_hard_max_100": counts["source_metadata_records"] <= MAX_RECORD_COUNT,
            "real_pixiv_source_prior_records_at_least_30": (
                counts["metadata_records_by_provider_and_data_type"].get(f"pixiv:{DATA_TYPE_REAL}", 0)
                >= REAL_PIXIV_SOURCE_PRIOR_MIN
            ),
            "apply_db_executed": bool(db_write_summary.get("apply")),
            "apply_db_success": bool(db_write_summary.get("success")),
            "forbidden_truth_table_write_count_zero": db_write_summary.get("forbidden_truth_table_write_count", 0) == 0,
            "pixiv_applicable_coverage_at_least_80": (pixiv.get("coverage") or 0) >= 0.8,
            "saucenao_applicable_coverage_at_least_90": (saucenao.get("coverage") or 0) >= 0.9,
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
            "llm_classification": False,
            "provider_upload": False,
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
            "merge_readiness": "ready_for_review_if_apply_db_and_minimums_pass",
        },
    }


def build_markdown_report(summary: Mapping[str, Any], *, private_markers: Iterable[str]) -> str:
    lines = [
        f"# {PHASE}: provider-neutral source name registry",
        "",
        "## Summary",
        "",
        f"- PR #91 merge confirmation: `{json.dumps(summary['pr91_merge_confirmation'], sort_keys=True)}`.",
        f"- Provider shapes covered: `{json.dumps(summary['provider_shapes_covered'], sort_keys=True)}`.",
        f"- Row counts: `{json.dumps(summary['row_counts'], sort_keys=True)}`.",
        f"- F5 minimum requirements: `{json.dumps(summary['f5_minimum_requirements'], sort_keys=True)}`.",
        f"- Pixiv applicable name coverage: `{summary['pixiv_applicable_name_coverage']}`.",
        f"- SauceNAO applicable name coverage: `{summary['saucenao_applicable_name_coverage']}`.",
        f"- Not applicable no-person-signal count: `{summary['not_applicable_no_person_signal_count']}`.",
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


def manual_review_guide() -> str:
    return "\n".join(
        [
            "# Phase 4.4-P2R-F5 manual review guide",
            "",
            "- SourceNameRegistry rows are searchable source observations, not Entities.",
            "- SourceNameAliasCandidate rows are candidate relations, not EntityAlias truth.",
            "- Strong aliases need provider canonical, curated, or trusted local/external provenance.",
            "- Parenthetical relations are useful medium evidence and should remain reviewable.",
            "- Co-occurrence, if added later, must stay weak and must not canonicalize search by itself.",
            "- No-tag/no-name providers should keep SourceMetadataRecord rows with zero tag/name observations.",
            "",
        ]
    )


def private_markers(bundle: SourceRegistryBundle, records: Sequence[Mapping[str, Any]]) -> list[str]:
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
    return sorted({marker for marker in markers if marker and len(marker) > 2}, key=len, reverse=True)


def search_validation_queries(bundle: SourceRegistryBundle) -> list[str]:
    queries: list[str] = []
    for name in bundle.name_observations[:8]:
        queries.append(name.raw_name)
    for alias in bundle.alias_candidates[:8]:
        queries.append(alias.source_display_name)
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


def db_apply_summary(args: argparse.Namespace, bundle: SourceRegistryBundle) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if args.no_db or args.dry_run or not args.apply_db:
        return None, {
            "apply": False,
            "reason": "no_db_or_dry_run_or_apply_db_not_set",
            "guard_installed": False,
            "planned": bundle_public_counts(bundle),
            "forbidden_truth_table_write_count": 0,
        }

    config = f1.load_project_config(ROOT)
    engine = create_engine(config.database_url)
    migrate_add_source_metadata_name_registry(engine, inspect(engine))
    install_source_registry_write_guard(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        db_identity = f1.prove_db_identity(session, config)
        forbidden_before = table_row_counts(session, FORBIDDEN_TABLES)
        allowed_before = table_row_counts(session, ALLOWED_WRITE_TABLES)
        write_summary = persist_source_registry_bundle(session, bundle, apply=True)
        forbidden_after = table_row_counts(session, FORBIDDEN_TABLES)
        allowed_after = table_row_counts(session, ALLOWED_WRITE_TABLES)
        forbidden_deltas = table_row_deltas(forbidden_before, forbidden_after)
        forbidden_delta_total = sum(abs(value) for value in forbidden_deltas.values())
        write_summary["allowed_table_row_counts_before"] = allowed_before
        write_summary["allowed_table_row_counts_after"] = allowed_after
        write_summary["allowed_table_row_deltas"] = table_row_deltas(allowed_before, allowed_after)
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
    parser.add_argument("--provider-name-coverage-csv", default=str(PRIVATE_PROVIDER_NAME_COVERAGE_CSV))
    parser.add_argument("--search-validation-csv", default=str(PRIVATE_SEARCH_VALIDATION_CSV))
    parser.add_argument("--curated-template-csv", default=str(PRIVATE_CURATED_TEMPLATE_CSV))
    parser.add_argument("--manual-review-guide", default=str(PRIVATE_MANUAL_REVIEW_GUIDE))
    parser.add_argument("--apply-db", action="store_true")
    parser.add_argument("--no-db", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--target-record-count", type=int, default=TARGET_RECORD_COUNT_DEFAULT)
    parser.add_argument("--real-pixiv-source-prior-min", type=int, default=REAL_PIXIV_SOURCE_PRIOR_MIN)
    parser.add_argument("--max-records", type=int, default=MAX_RECORD_COUNT)
    parser.add_argument("--disable-scale-up", action="store_true")
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = resolve_repo_path(args.output_dir)
    private_paths = [
        resolve_repo_path(args.details_json),
        resolve_repo_path(args.source_metadata_csv),
        resolve_repo_path(args.source_tag_observations_csv),
        resolve_repo_path(args.source_tag_registry_csv),
        resolve_repo_path(args.source_name_observations_csv),
        resolve_repo_path(args.source_name_registry_csv),
        resolve_repo_path(args.source_name_alias_candidates_csv),
        resolve_repo_path(args.provider_name_coverage_csv),
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

    records, input_summary = load_provider_records(args.input_json or None)
    records, scale_summary = scale_provider_records(records, args)
    input_summary = {**dict(input_summary), "scale_up": scale_summary}
    curated_mappings = load_curated_mappings(args.curated_mapping_input or None)
    bundle = build_source_registry_bundle(records, curated_mappings=curated_mappings)
    search_rows = validate_search_queries(bundle, search_validation_queries(bundle))
    db_identity, db_write_summary = db_apply_summary(args, bundle)
    summary = build_public_summary(
        records=records,
        bundle=bundle,
        input_summary=input_summary,
        curated_mapping_count=len(curated_mappings),
        db_identity=db_identity,
        db_write_summary=db_write_summary,
        search_rows=search_rows,
    )
    markers = private_markers(bundle, records)
    f1.assert_public_payload_safe(summary, private_markers=markers)
    report = build_markdown_report(summary, private_markers=markers)

    write_public_json(resolve_repo_path(args.report_json), summary)
    write_public_text(resolve_repo_path(args.report_md), report, private_markers=markers)
    write_private_json(
        resolve_repo_path(args.details_json),
        {
            "private_exact_names_and_tags": True,
            "records_private": list(records),
            "source_metadata_records": [asdict(row) for row in bundle.metadata_records],
            "source_tag_observations": [asdict(row) for row in bundle.tag_observations],
            "source_tag_registry": [asdict(row) for row in bundle.tag_registry],
            "source_name_observations": [asdict(row) for row in bundle.name_observations],
            "source_name_registry": [asdict(row) for row in bundle.name_registry],
            "source_name_alias_candidates": [asdict(row) for row in bundle.alias_candidates],
            "source_metadata_evidence": [asdict(row) for row in bundle.evidence],
            "name_search_index": build_name_search_index(bundle),
            "summary_public": summary,
        },
    )
    write_private_text(resolve_repo_path(args.source_metadata_csv), _csv(asdict(row) for row in bundle.metadata_records))
    write_private_text(resolve_repo_path(args.source_tag_observations_csv), _csv(asdict(row) for row in bundle.tag_observations))
    write_private_text(resolve_repo_path(args.source_tag_registry_csv), _csv(asdict(row) for row in bundle.tag_registry))
    write_private_text(resolve_repo_path(args.source_name_observations_csv), _csv(asdict(row) for row in bundle.name_observations))
    write_private_text(resolve_repo_path(args.source_name_registry_csv), _csv(asdict(row) for row in bundle.name_registry))
    write_private_text(resolve_repo_path(args.source_name_alias_candidates_csv), _csv(asdict(row) for row in bundle.alias_candidates))
    write_private_text(resolve_repo_path(args.provider_name_coverage_csv), _coverage_csv(provider_name_coverage(bundle)))
    write_private_text(resolve_repo_path(args.search_validation_csv), _csv(search_rows))
    write_private_text(resolve_repo_path(args.curated_template_csv), curated_mapping_template_csv())
    write_private_text(resolve_repo_path(args.manual_review_guide), manual_review_guide())

    return {
        "report_md": _rel(resolve_repo_path(args.report_md)),
        "report_json": _rel(resolve_repo_path(args.report_json)),
        "source_metadata_records": len(bundle.metadata_records),
        "source_name_registry": len(bundle.name_registry),
        "alias_candidates": len(bundle.alias_candidates),
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
