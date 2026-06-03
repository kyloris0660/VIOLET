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
    provider_record_lookup_key,
    provider_name_coverage,
    raw_applicable_signal_rows,
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
TARGET_RECORD_COUNT_DEFAULT = 100
MIN_RECORD_COUNT = 75
REAL_PIXIV_SOURCE_PRIOR_MIN = 50
REAL_PIXIV_APPLICABLE_NAME_SIGNAL_TARGET = 20
SAUCENAO_ARTIFACT_RECORD_TARGET = 10
MAX_RECORD_COUNT = 150

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
CANONICAL_LOCAL_ROOT = Path.home() / "Documents" / "AnimeLocalBooru"
CANONICAL_LOCAL_MANIFESTS = CANONICAL_LOCAL_ROOT / ".local_manifests"


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

    real_pixiv_records: list[dict[str, Any]] = []
    real_pixiv_summary: dict[str, Any] = {"attempted": False, "record_count": 0}
    if not args.no_db and not args.dry_run:
        real_pixiv_records, real_pixiv_summary = load_real_pixiv_source_prior_records_from_db(
            limit=max(int(args.real_pixiv_source_prior_min), REAL_PIXIV_SOURCE_PRIOR_MIN)
        )
    add_many(real_pixiv_records, source_bucket="real_pixiv_source_prior_db")

    pixiv_artifact_records, pixiv_artifact_summary = load_existing_pixiv_artifact_records(
        limit=max(REAL_PIXIV_APPLICABLE_NAME_SIGNAL_TARGET, 20)
    )
    add_many(pixiv_artifact_records, source_bucket="pixiv_existing_artifact_or_gallery_dl")
    artifact_saucenao_records = load_existing_saucenao_artifact_records(limit=10)
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
        "existing_pixiv_artifact_summary": pixiv_artifact_summary,
        "existing_saucenao_artifact_record_count": added_counts.get("saucenao_existing_artifact_or_report", 0),
        "existing_saucenao_artifact_target": SAUCENAO_ARTIFACT_RECORD_TARGET,
        "fixture_or_mock_record_count": data_type_counts.get(DATA_TYPE_FIXTURE, 0),
        "meets_scale_minimum": len(final_records) >= MIN_RECORD_COUNT,
        "meets_real_pixiv_source_prior_minimum": (
            provider_data_type_counts.get(f"pixiv:{DATA_TYPE_REAL}", 0)
            >= int(args.real_pixiv_source_prior_min)
        ),
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
                "data_type_label": DATA_TYPE_REAL,
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
            normalized = normalize_source_text(text_value)
            if normalized:
                rows.append(normalized)
        return rows
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


def install_source_registry_write_guard(engine) -> None:
    write_re = re.compile(r"^\s*(insert|update|delete|alter|drop|truncate|create|merge|replace|copy)\b", re.IGNORECASE)
    destructive_re = re.compile(r"^\s*(delete|drop|truncate|merge|replace|copy|alter|create)\b", re.IGNORECASE)
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
    coverage_by_type = coverage_by_provider_and_data_type(bundle)
    pixiv = coverage["providers"].get("pixiv", {})
    saucenao = coverage["providers"].get("saucenao", {})
    pixiv_real = combined_provider_coverage(coverage_by_type, provider="pixiv", data_type_labels=[DATA_TYPE_REAL])
    saucenao_real_or_artifact = combined_provider_coverage(
        coverage_by_type,
        provider="saucenao",
        data_type_labels=[DATA_TYPE_REAL, DATA_TYPE_ARTIFACT],
    )
    real_pixiv_sample_min_met = pixiv_real["record_count"] >= REAL_PIXIV_SOURCE_PRIOR_MIN
    real_pixiv_applicable_min_met = (
        pixiv_real["applicable_name_signal_count"] >= REAL_PIXIV_APPLICABLE_NAME_SIGNAL_TARGET
    )
    real_pixiv_coverage_met = (
        pixiv_real["coverage"] is not None
        and pixiv_real["coverage"] >= 0.8
        and real_pixiv_applicable_min_met
    )
    saucenao_artifact_sample_min_met = saucenao_real_or_artifact["record_count"] >= SAUCENAO_ARTIFACT_RECORD_TARGET
    saucenao_non_fixture_coverage_met = (
        saucenao_real_or_artifact["coverage"] is not None
        and saucenao_real_or_artifact["coverage"] >= 0.9
        and saucenao_artifact_sample_min_met
    )
    apply_db_success = bool(db_write_summary.get("apply")) and bool(db_write_summary.get("success"))
    scale_minimum_met = counts["source_metadata_records"] >= MIN_RECORD_COUNT
    hard_max_respected = counts["source_metadata_records"] <= MAX_RECORD_COUNT
    forbidden_delta_zero = db_write_summary.get("forbidden_truth_table_write_count", 0) == 0
    f5_minimum_stage_goal_met = all(
        [
            scale_minimum_met,
            hard_max_respected,
            real_pixiv_sample_min_met,
            real_pixiv_coverage_met,
            saucenao_non_fixture_coverage_met,
            apply_db_success,
            forbidden_delta_zero,
        ]
    )
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
        "coverage_by_provider_and_data_type": coverage_by_type,
        "expanded_real_data_validation": {
            "real_pixiv": pixiv_real,
            "real_pixiv_source_prior_records_at_least_50": real_pixiv_sample_min_met,
            "real_pixiv_applicable_name_signal_target": REAL_PIXIV_APPLICABLE_NAME_SIGNAL_TARGET,
            "real_pixiv_applicable_name_signal_sample_met": real_pixiv_applicable_min_met,
            "real_pixiv_applicable_name_coverage_at_least_80": real_pixiv_coverage_met,
            "saucenao_real_or_artifact": saucenao_real_or_artifact,
            "saucenao_real_or_artifact_records_at_least_10": saucenao_artifact_sample_min_met,
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
            "source_metadata_record_candidates_preferred_100": counts["source_metadata_records"] >= TARGET_RECORD_COUNT_DEFAULT,
            "source_metadata_record_candidates_hard_max_150": hard_max_respected,
            "real_pixiv_source_prior_records_at_least_50": real_pixiv_sample_min_met,
            "real_pixiv_applicable_name_signal_records_at_least_20": real_pixiv_applicable_min_met,
            "apply_db_executed": bool(db_write_summary.get("apply")),
            "apply_db_success": apply_db_success,
            "forbidden_truth_table_write_count_zero": forbidden_delta_zero,
            "pixiv_real_applicable_coverage_at_least_80": real_pixiv_coverage_met,
            "saucenao_real_or_artifact_applicable_coverage_at_least_90": saucenao_non_fixture_coverage_met,
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
        f"- PR #91 merge confirmation: `{json.dumps(summary['pr91_merge_confirmation'], sort_keys=True)}`.",
        f"- Provider shapes covered: `{json.dumps(summary['provider_shapes_covered'], sort_keys=True)}`.",
        f"- Row counts: `{json.dumps(summary['row_counts'], sort_keys=True)}`.",
        f"- F5 minimum requirements: `{json.dumps(summary['f5_minimum_requirements'], sort_keys=True)}`.",
        f"- Pixiv applicable name coverage: `{summary['pixiv_applicable_name_coverage']}`.",
        f"- SauceNAO applicable name coverage: `{summary['saucenao_applicable_name_coverage']}`.",
        f"- Not applicable no-person-signal count: `{summary['not_applicable_no_person_signal_count']}`.",
        f"- Expanded real/artifact validation: `{json.dumps(summary['expanded_real_data_validation'], sort_keys=True)}`.",
        f"- Reviewer fixes applied: `provider_scoped_metadata_key, registry_merge, numeric_booru_category, saucenao_work_or_copyright, stale_observation_retirement, source_tag_registry_merge, raw_denominator_coverage, complete_write_guard, post_truncation_scale_metrics`.",
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
    parser.add_argument("--raw-applicable-name-signals-csv", default=str(PRIVATE_RAW_APPLICABLE_NAME_SIGNALS_CSV))
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
            "raw_applicable_name_signals": raw_applicable_signal_rows(bundle),
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
    write_private_text(resolve_repo_path(args.raw_applicable_name_signals_csv), _csv(raw_applicable_signal_rows(bundle)))
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
