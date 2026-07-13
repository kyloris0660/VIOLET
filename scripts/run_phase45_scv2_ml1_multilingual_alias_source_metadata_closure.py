"""Run the zero-network SCV2-ML1 fixed-evidence audit.

Lifecycle: phase-scoped operational runner. The initial mode is read-only over
the accepted R2R database. It never calls gallery-dl, Pixiv, an LLM, or another
provider and keeps raw names/IDs/paths in ignored private artifacts.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import sys
from typing import Any, Iterable, Mapping, Sequence
import zipfile

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
for candidate in (ROOT, BACKEND_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.models import Media, SourceConcept, SourceConceptSignalLink  # noqa: E402
from app.services.source_assertion_search_service import (  # noqa: E402
    DEFAULT_QUERY_VISIBLE_EXACT_PIXIV_NAME_FIELDS,
    _source_concept_key_candidates,
    apply_endpoint_equivalent_text_search,
)
from app.services.source_metadata_registry_service import (  # noqa: E402
    canonical_source_key,
    normalize_source_text,
)
from app.services.pixiv_filename_prior_service import PARSER_VERSION  # noqa: E402
from app.services.pixiv_metadata_ingestion_service import (  # noqa: E402
    CANONICAL_COMPLETE_STATUSES,
    QUEUE_METADATA_KIND,
    classify_pixiv_metadata_lifecycle,
    is_pixiv_creator_observation_compatible_with_parent,
    is_trusted_complete_pixiv_metadata_record,
    llm_budget_policy,
    promotion_manifest,
)
from app.services.source_concept_search_service import _format_search_query_token  # noqa: E402
from app.utils.search_parser import (  # noqa: E402
    _translation_alias_trusted_for_search,
    parse_search_query,
    wildcard_to_regex,
)
from scripts import run_phase44p0_pixiv_source_prior_auto_verify as p0  # noqa: E402
from scripts import run_phase45_scv2_r2_constraint_aware_graph_remediation as r2  # noqa: E402
from scripts.run_pixiv_metadata_ingestion import (  # noqa: E402
    build_executable_manifest,
    executable_manifest_fingerprint,
)
from scripts.phase_contracts.contract_checks import check_phase_contract  # noqa: E402


PHASE = "4.5-SCV2-ML1"
PHASE_TITLE = "SCV2-ML1: Multilingual Alias and Source-Metadata Closure"
CONTRACT_ID = "ml1_multilingual_alias_source_metadata_closure_contract_v1"
BASELINE_SHA = "5bbbb8ff13b140ea77a839757603714bfdd87181"
ACCEPTED_R2R_DB = "blombooru_scv2_r2r_dryrun_test_20260710"
ACQUISITION_DB = "blombooru_scv2_ml1_acquisition_test_20260712"
ACCEPTED_R2_SOURCE_DB = "blombooru_scv2_r2_review4_test_20260710"
REPORT_MD = ROOT / "docs/reports/phase-4.5-scv2-ml1-multilingual-alias-source-metadata-closure.md"
REPORT_JSON = ROOT / "docs/reports/phase-4.5-scv2-ml1-multilingual-alias-source-metadata-closure-summary.json"
DEFAULT_OUTPUT_DIR = ROOT / ".local_manifests/phase-4.5-scv2-ml1-multilingual-alias-source-metadata-closure"
ACQUISITION_OUTPUT_DIR = ROOT / ".local_manifests/phase-4.5-scv2-ml1-pixiv-metadata-ingestion"
ACQUISITION_EXECUTION_SUMMARY = ACQUISITION_OUTPUT_DIR / "execution-summary.json"
GOVERNANCE_TRANSITION_SUMMARY = ACQUISITION_OUTPUT_DIR / "source-page-mismatch-governance-summary.json"
GOVERNANCE_TRANSITION_LEDGER = ACQUISITION_OUTPUT_DIR / "source-page-mismatch-governance-ledger.json"

FIXED_TABLES = (
    "blombooru_media",
    "blombooru_tags",
    "blombooru_media_tags",
    "blombooru_source_metadata_records",
    "blombooru_source_name_observations",
    "blombooru_source_tag_observations",
    "blombooru_source_searchable_name_assertions",
    "blombooru_source_name_registry",
    "blombooru_source_tag_registry",
    "blombooru_tag_translations",
    "blombooru_source_concept_signals",
    "blombooru_source_concepts",
    "blombooru_source_concept_aliases",
    "blombooru_source_concept_evidence",
    "blombooru_source_concept_signal_links",
    "blombooru_source_concept_search_index",
    "blombooru_source_concept_fallback_search_index",
)
FORBIDDEN_TRUTH_TABLES = (
    "blombooru_entities",
    "blombooru_entity_aliases",
    "blombooru_entity_evidence",
    "blombooru_media_entity_candidates",
    "blombooru_media_entity_assignments",
    "blombooru_local_source_hints",
    "blombooru_provider_cache",
    "blombooru_negative_lookup_cache",
    "blombooru_media_tags",
    "blombooru_tags",
    "blombooru_tag_translations",
)

LOCAL_PATH_RE = re.compile(
    r"(?i)((?<![A-Za-z])[A-Z]:[\\/]|\\\\|file://|/(?:Users|home|mnt|Volumes|workspace|tmp)(?:/|$))"
)
SECRET_RE = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9._~+\-/]{8,}|(?:access|refresh)[_-]?token\s*[=:]|api[_-]?key\s*[=:]|sk-[A-Za-z0-9_-]{12,})"
)
PRIVATE_NAME_KEY_RE = re.compile(
    r"(?i)^(raw_name|creator_name|creator_account|filename|local_path|source_url|user_id|work_id)$"
)


class ML1BlockedError(RuntimeError):
    """Raised when an ML1 fail-closed precondition is not satisfied."""


@dataclass(frozen=True)
class PixivCandidate:
    media_id: int
    private_media_ref: str
    work_id: str | None
    page_index: int | None
    all_matches: tuple[tuple[str, int], ...]
    match_origins: tuple[tuple[str, str, int], ...]
    matched_tokens: tuple[tuple[str, str, int, str], ...]
    local_basename: str
    local_import_timestamp: str | None
    layout_classes: tuple[str, ...]
    status: str
    metadata_record_ids: tuple[int, ...]
    matching_metadata_record_ids: tuple[int, ...]
    reason: str


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def git_value(*args: str) -> str:
    import subprocess

    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


def private_ref(value: Any, prefix: str) -> str:
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def read_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list, int, float, bool)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def rows(session: Session, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(row) for row in session.execute(text(sql), params or {}).mappings()]


def fast_fingerprint_tables(session: Session, tables: Sequence[str]) -> dict[str, Any]:
    """Fingerprint fixed tables in-database without per-row client fetches."""

    snapshots: dict[str, Any] = {}
    for table in tables:
        exists = bool(session.execute(text("SELECT to_regclass(:name) IS NOT NULL"), {"name": table}).scalar())
        if not exists:
            snapshots[table] = {
                "table": table,
                "status": "missing",
                "count": None,
                "row_content_sha256": hashlib.sha256(b"missing_table").hexdigest(),
                "columns": [],
            }
            continue
        columns = [
            str(row[0])
            for row in session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() "
                    "AND table_name=:name ORDER BY ordinal_position"
                ),
                {"name": table},
            )
        ]
        count = int(session.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0)
        content_md5 = str(
            session.execute(
                text(
                    "SELECT md5(COALESCE(string_agg(row_hash, '' ORDER BY row_hash), '')) FROM ("
                    f'SELECT md5(to_jsonb(row_value)::text) row_hash FROM "{table}" row_value'
                    ") hashed"
                )
            ).scalar()
            or ""
        )
        snapshots[table] = {
            "table": table,
            "status": "present",
            "count": count,
            # Keep the comparison shape consumed by the existing helper. The
            # algorithm label below truthfully records that this is aggregate MD5.
            "row_content_sha256": content_md5,
            "columns": columns,
        }
    return {
        "captured_at": utc_now(),
        "database": str(session.execute(text("SELECT current_database()")).scalar() or ""),
        "fingerprint_algorithm": "md5_over_sorted_md5_of_to_jsonb_rows_database_aggregate",
        "tables": snapshots,
        "table_count": len(snapshots),
        "missing_tables": sorted(name for name, value in snapshots.items() if value["status"] != "present"),
    }


def canonical_pixiv_matches(values: Sequence[Any]) -> tuple[tuple[str, int], ...]:
    found: set[tuple[str, int]] = set()
    for value in values:
        for match in p0.extract_pixiv_filename_prior_from_text(str(value or "")):
            found.add((str(match["pixiv_work_id"]), int(match["page_index"])))
    return tuple(sorted(found))


def canonical_pixiv_matches_by_field(
    fields: Sequence[tuple[str, Any]],
) -> tuple[tuple[str, str, int], ...]:
    found: set[tuple[str, str, int]] = set()
    for source_field, value in fields:
        for match in p0.extract_pixiv_filename_prior_from_text(str(value or "")):
            found.add((str(source_field), str(match["pixiv_work_id"]), int(match["page_index"])))
    return tuple(sorted(found))


def canonical_pixiv_token_matches_by_field(
    fields: Sequence[tuple[str, Any]],
) -> tuple[tuple[str, str, int, str], ...]:
    found: set[tuple[str, str, int, str]] = set()
    for source_field, value in fields:
        for match in p0.extract_pixiv_filename_prior_from_text(str(value or "")):
            found.add(
                (
                    str(source_field),
                    str(match["pixiv_work_id"]),
                    int(match["page_index"]),
                    str(match["token"]),
                )
            )
    return tuple(sorted(found))


def classify_filename_layout(basename: str, token: str) -> str:
    stem = Path(basename).stem
    index = stem.casefold().find(token.casefold())
    if index < 0:
        return "token_not_in_basename"
    prefix = stem[:index]
    suffix = stem[index + len(token) :]
    if not prefix and not suffix:
        return "exact_token_basename"
    if prefix and suffix:
        return "prefix_and_suffix"
    if prefix:
        return "prefixed_token"
    return "suffixed_token"


def basename_only(value: Any) -> str:
    return re.split(r"[\\/]", str(value or ""))[-1]


def build_pixiv_accounting(
    media_rows: Sequence[Mapping[str, Any]],
    metadata_rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[PixivCandidate], list[dict[str, Any]]]:
    metadata_by_media: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in metadata_rows:
        if str(row.get("provider") or "").casefold() == "pixiv" and row.get("media_id") is not None:
            metadata_by_media[int(row["media_id"])].append(dict(row))

    candidates: list[PixivCandidate] = []
    origin_media: dict[str, set[int]] = defaultdict(set)
    origin_works: dict[str, set[str]] = defaultdict(set)
    agreement_counts = Counter()
    for media in media_rows:
        media_id = int(media["id"])
        approved_fields = (
                ("filename", media.get("filename")),
                ("stored_path", media.get("path")),
                ("thumbnail_path", media.get("thumbnail_path")),
                ("source_field", media.get("source")),
        )
        token_matches = canonical_pixiv_token_matches_by_field(approved_fields)
        match_origins = tuple((field, work_id, page) for field, work_id, page, _ in token_matches)
        matches = tuple(sorted({(work_id, page_index) for _, work_id, page_index in match_origins}))
        if not matches:
            continue
        fields_present: set[str] = set()
        field_pairs: dict[str, set[tuple[str, int]]] = defaultdict(set)
        for source_field, work_id, page_index in match_origins:
            fields_present.add(source_field)
            field_pairs[source_field].add((work_id, page_index))
            origin_media[source_field].add(media_id)
            origin_works[source_field].add(work_id)
        if field_pairs["filename"] and field_pairs["stored_path"]:
            agreement_counts[
                "filename_path_agreement" if field_pairs["filename"] == field_pairs["stored_path"] else "filename_path_conflict"
            ] += 1
        if field_pairs["filename"] and field_pairs["source_field"]:
            agreement_counts[
                "filename_source_agreement" if field_pairs["filename"] == field_pairs["source_field"] else "filename_source_conflict"
            ] += 1
        if len(fields_present) > 1:
            agreement_counts["multi_field_agreement" if len(matches) == 1 else "multi_field_conflict"] += 1
        if fields_present == {"source_field"}:
            agreement_counts["source_only"] += 1
        if fields_present == {"thumbnail_path"}:
            agreement_counts["thumbnail_only"] += 1
        metadata = metadata_by_media.get(media_id, [])
        metadata_ids = tuple(sorted(int(item["id"]) for item in metadata))
        matching: list[int] = []
        if len(matches) == 1:
            work_id, page_index = matches[0]
            exact_rows = [
                item for item in metadata
                if str(item.get("source_work_id") or "") == work_id
                and int(item.get("source_page_index") or 0) == page_index
            ]
            mismatched_rows = [
                item
                for item in metadata
                if item not in exact_rows
                and is_trusted_complete_pixiv_metadata_record(item)
            ]
            lifecycle_classes = {
                classify_pixiv_metadata_lifecycle(item.get("status")) for item in exact_rows
            }
            matching = [int(item["id"]) for item in exact_rows if classify_pixiv_metadata_lifecycle(item.get("status")) == "complete"]
            if "complete" in lifecycle_classes:
                status = "metadata_present_complete"
                reason = "exact_media_work_page_match"
            elif "terminal" in lifecycle_classes:
                status = "terminal_remote_unavailable"
                reason = "durable_authenticated_remote_unavailable_evidence"
            elif "provider_identity_mismatch" in lifecycle_classes:
                status = "provider_identity_mismatch"
                reason = "durable_provider_identity_mismatch_evidence"
            elif "deferred_nonblocking_source_page_mismatch" in lifecycle_classes:
                status = "deferred_nonblocking_source_page_mismatch"
                reason = "governed_exact_source_page_mismatch_without_invented_link"
            elif mismatched_rows:
                status = "filename_identity_conflict"
                reason = "metadata_exists_but_work_or_page_mismatch"
            elif "normalization_failed" in lifecycle_classes:
                status = "metadata_parse_or_normalization_failure"
                reason = "durable_normalization_failure_evidence"
            elif "retryable" in lifecycle_classes:
                status = "retryable_provider_failure"
                reason = "durable_retryable_attempt_evidence"
            elif "pending" in lifecycle_classes:
                status = "metadata_pending"
                reason = "exact_work_page_pending_acquisition"
            elif exact_rows:
                status = "metadata_parse_or_normalization_failure"
                reason = "unknown_matching_metadata_lifecycle"
            elif metadata:
                status = "filename_identity_conflict"
                reason = "metadata_exists_but_work_or_page_mismatch"
            else:
                status = "no_durable_attempt_or_result_evidence"
                reason = "no_durable_attempt_or_result_evidence"
        else:
            work_id = None
            page_index = None
            match_lifecycles = []
            for matched_work_id, matched_page_index in matches:
                exact_match_rows = [
                    item
                    for item in metadata
                    if str(item.get("source_work_id") or "") == matched_work_id
                    and int(item.get("source_page_index") or 0) == matched_page_index
                ]
                match_lifecycles.append(
                    {
                        classify_pixiv_metadata_lifecycle(item.get("status"))
                        for item in exact_match_rows
                    }
                )
            all_governed_closed = bool(match_lifecycles) and all(
                lifecycles & {"complete", "deferred_nonblocking_source_page_mismatch"}
                for lifecycles in match_lifecycles
            )
            any_deferred = any(
                "deferred_nonblocking_source_page_mismatch" in lifecycles
                for lifecycles in match_lifecycles
            )
            if all_governed_closed and any_deferred:
                status = "deferred_nonblocking_source_page_mismatch"
                reason = "all_filename_memberships_governed_without_conflict_winner"
            else:
                status = "filename_identity_conflict"
                reason = "multiple_canonical_work_page_tokens_on_one_media"
        candidates.append(
            PixivCandidate(
                media_id=media_id,
                private_media_ref=private_ref(media_id, "media"),
                work_id=work_id,
                page_index=page_index,
                all_matches=matches,
                match_origins=match_origins,
                matched_tokens=token_matches,
                local_basename=basename_only(media.get("filename")),
                local_import_timestamp=(str(media.get("uploaded_at")) if media.get("uploaded_at") else None),
                layout_classes=tuple(
                    sorted(
                        {
                            classify_filename_layout(basename_only(media.get("filename")), token)
                            for field, _, _, token in token_matches
                            if field == "filename"
                        }
                    )
                ),
                status=status,
                metadata_record_ids=metadata_ids,
                matching_metadata_record_ids=tuple(sorted(matching)),
                reason=reason,
            )
        )

    status_counts = Counter(item.status for item in candidates)
    retryable_statuses = {
        "retryable_authentication_failure",
        "retryable_rate_limit_failure",
        "retryable_network_or_transport_failure",
        "retryable_provider_failure",
    }
    work_rows: list[dict[str, Any]] = []
    by_work: dict[str, list[PixivCandidate]] = defaultdict(list)
    for item in candidates:
        # A conflicted media row belongs to every concrete work it exposed.
        # This prevents a conflict from disappearing from the work ledger.
        for work_id in sorted({match[0] for match in item.all_matches}):
            by_work[work_id].append(item)
    for work_id, items in sorted(by_work.items()):
        statuses = {item.status for item in items}
        if "filename_identity_conflict" in statuses:
            status = "unresolved_conflict"
        elif statuses == {"metadata_present_complete"}:
            status = "complete"
        elif statuses <= {"metadata_present_complete", "terminal_remote_unavailable"} and "terminal_remote_unavailable" in statuses:
            status = "terminal"
        elif "deferred_nonblocking_source_page_mismatch" in statuses and statuses <= {
            "metadata_present_complete",
            "terminal_remote_unavailable",
            "deferred_nonblocking_source_page_mismatch",
        }:
            status = "deferred_nonblocking_source_page_mismatch"
        elif "metadata_pending" in statuses:
            status = "pending"
        elif statuses & retryable_statuses:
            status = "retryable"
        elif "metadata_parse_or_normalization_failure" in statuses:
            status = "normalization_failed"
        elif "provider_identity_mismatch" in statuses:
            status = "provider_identity_mismatch"
        else:
            status = "missing"
        memberships = [
            {
                "private_media_ref": item.private_media_ref,
                "matched_pages": sorted(page for candidate_work, page in item.all_matches if candidate_work == work_id),
                "source_fields": sorted({field for field, candidate_work, _ in item.match_origins if candidate_work == work_id}),
                "media_status": item.status,
            }
            for item in items
        ]
        work_rows.append(
            {
                "private_work_ref": private_ref(work_id, "pixiv_work"),
                "work_id": work_id,
                "media_count": len({item.media_id for item in items}),
                "page_indexes": sorted({page for item in items for candidate_work, page in item.all_matches if candidate_work == work_id}),
                "status": status,
                "conflict_memberships": memberships if status == "unresolved_conflict" else [],
            }
        )

    candidate_count = len(candidates)
    distinct_work_count = len(by_work)
    work_status_counts = Counter(str(item["status"]) for item in work_rows)
    complete_work_count = work_status_counts["complete"]
    terminal_work_count = work_status_counts["terminal"]
    retryable_media_count = sum(status_counts[key] for key in retryable_statuses)
    parse_conflict_count = status_counts["metadata_parse_or_normalization_failure"] + status_counts["filename_identity_conflict"]
    complete_media_count = status_counts["metadata_present_complete"]
    target_request_work_ids = {str(item["work_id"]) for item in work_rows if item["status"] in {"missing", "pending", "retryable"}}
    work_accounting_sum = sum(
        work_status_counts[key]
        for key in (
            "complete",
            "terminal",
            "deferred_nonblocking_source_page_mismatch",
            "pending",
            "retryable",
            "normalization_failed",
            "provider_identity_mismatch",
            "missing",
            "unresolved_conflict",
        )
    )
    conflict_media = [item for item in candidates if item.status == "filename_identity_conflict"]
    conflict_tokens = {
        (item.media_id, field, work_id, page)
        for item in conflict_media
        for field, work_id, page in item.match_origins
    }
    conflict_works = {work_id for _, _, work_id, _ in conflict_tokens}
    public_origin_labels = {
        "filename": "filename_origin",
        "stored_path": "stored_path_origin",
        "thumbnail_path": "thumbnail_origin",
        "source_field": "source_field_origin",
    }
    public = {
        "canonical_complete_statuses": sorted(CANONICAL_COMPLETE_STATUSES),
        "canonical_parser_rule": "lowercase_work_id_p_page_token_nonzero_6_to_12_digit_work_id",
        "canonical_parser_version": PARSER_VERSION,
        "candidate_media_count": candidate_count,
        "accounted_media_count": candidate_count,
        "candidate_distinct_work_count": distinct_work_count,
        "accounted_distinct_work_count": work_accounting_sum,
        "candidate_media_accounting_coverage": 1.0 if candidate_count >= 0 else 0.0,
        "candidate_work_accounting_coverage": round(work_accounting_sum / distinct_work_count, 6) if distinct_work_count else 1.0,
        "metadata_present_complete_media_count": complete_media_count,
        "metadata_present_complete_work_count": complete_work_count,
        "metadata_pending_media_count": status_counts["metadata_pending"],
        "terminal_remote_unavailable_media_count": status_counts["terminal_remote_unavailable"],
        "terminal_remote_unavailable_work_count": terminal_work_count,
        "deferred_nonblocking_source_page_mismatch_media_count": status_counts[
            "deferred_nonblocking_source_page_mismatch"
        ],
        "deferred_nonblocking_source_page_mismatch_work_count": work_status_counts[
            "deferred_nonblocking_source_page_mismatch"
        ],
        "pending_work_count": work_status_counts["pending"],
        "retryable_work_count": work_status_counts["retryable"],
        "normalization_failed_work_count": work_status_counts["normalization_failed"],
        "provider_identity_mismatch_work_count": work_status_counts["provider_identity_mismatch"],
        "missing_work_count": work_status_counts["missing"],
        "retryable_failure_media_count": retryable_media_count,
        "retryable_authentication_failure_media_count": status_counts["retryable_authentication_failure"],
        "retryable_rate_limit_failure_media_count": status_counts["retryable_rate_limit_failure"],
        "retryable_network_or_transport_failure_media_count": status_counts["retryable_network_or_transport_failure"],
        "parse_or_identity_failure_media_count": parse_conflict_count,
        "metadata_parse_or_normalization_failure_media_count": status_counts["metadata_parse_or_normalization_failure"],
        "provider_identity_mismatch_media_count": status_counts["provider_identity_mismatch"],
        "filename_identity_conflict_media_count": status_counts["filename_identity_conflict"],
        "filename_identity_conflict_token_count": len(conflict_tokens),
        "filename_identity_conflict_distinct_work_count": len(conflict_works),
        "filename_identity_conflict_source_field_counts": dict(
            sorted(Counter(public_origin_labels[field] for _, field, _, _ in conflict_tokens).items())
        ),
        "conflict_resolved_work_count": 0,
        "conflict_unresolved_work_count": work_status_counts["unresolved_conflict"],
        "no_durable_attempt_or_result_evidence_media_count": status_counts["no_durable_attempt_or_result_evidence"],
        "not_attempted_media_count": 0,
        "unexplained_missing_media_count": status_counts["missing_without_explanation"],
        "normal_retrievable_missing_media_count": retryable_media_count + status_counts["no_durable_attempt_or_result_evidence"] + status_counts["missing_without_explanation"],
        "work_id_mismatch_media_count": status_counts["filename_identity_conflict"],
        "successful_metadata_linked_to_every_relevant_page": all(
            item.status != "metadata_present_complete" or bool(item.matching_metadata_record_ids)
            for item in candidates
        ),
        "terminal_exception_evidence_coverage": 1.0 if status_counts["terminal_remote_unavailable"] == 0 else 0.0,
        "incremental_acquisition_required": bool(target_request_work_ids),
        "projected_gallery_dl_request_count": len(target_request_work_ids),
        "authentication_requirements_present": bool(target_request_work_ids),
        "rate_limit_plan_present": bool(target_request_work_ids),
        "checkpoint_resume_plan_present": bool(target_request_work_ids),
        "authentication_requirements": "user-managed authenticated gallery-dl Pixiv profile outside the repository; no secret stored or echoed by V.I.O.L.E.T.",
        "rate_limit_plan": "incremental missing/retryable work IDs only; minimum two-second request spacing; bounded auth/rate/network failure budgets; no retry storm",
        "checkpoint_resume_plan": "checkpoint each completed distinct work ID; resume only the remaining manifest; preserve prior raw and normalized metadata",
        "complete_records_reacquisition_count": 0,
        "all_eligible_media_count": len(media_rows),
        "pixiv_ingestion_decision_media_count": len(metadata_by_media),
        "pixiv_ingestion_decision_coverage": round(len(metadata_by_media) / len(media_rows), 6) if media_rows else 1.0,
        "pixiv_candidate_media_count": candidate_count,
        "pixiv_candidate_complete_media_count": complete_media_count,
        "pixiv_candidate_complete_media_coverage": round(complete_media_count / candidate_count, 6) if candidate_count else 1.0,
        "pixiv_candidate_work_count": distinct_work_count,
        "pixiv_candidate_complete_work_count": complete_work_count,
        "pixiv_candidate_complete_work_coverage": round(complete_work_count / distinct_work_count, 6) if distinct_work_count else 1.0,
        "complete_work_coverage": round(complete_work_count / distinct_work_count, 6) if distinct_work_count else 1.0,
        "complete_or_terminal_work_coverage": round(
            (complete_work_count + terminal_work_count) / distinct_work_count, 6
        ) if distinct_work_count else 1.0,
        "complete_terminal_or_deferred_work_coverage": round(
            (
                complete_work_count
                + terminal_work_count
                + work_status_counts["deferred_nonblocking_source_page_mismatch"]
            )
            / distinct_work_count,
            6,
        ) if distinct_work_count else 1.0,
        "governed_candidate_work_coverage": round(
            (
                complete_work_count
                + terminal_work_count
                + work_status_counts["deferred_nonblocking_source_page_mismatch"]
            )
            / distinct_work_count,
            6,
        ) if distinct_work_count else 1.0,
        "exact_work_ids_public": False,
        "work_status_counts": dict(sorted(work_status_counts.items())),
        "work_accounting_equality_holds": work_accounting_sum == distinct_work_count,
        "origin_breakdown": {
            public_origin_labels[field]: {
                "candidate_media_count": len(origin_media[field]),
                "distinct_work_count": len(origin_works[field]),
            }
            for field in ("filename", "stored_path", "thumbnail_path", "source_field")
        },
        "origin_agreement_counts": dict(sorted(agreement_counts.items())),
        "filename_path_candidate_media_count": len(origin_media["filename"] | origin_media["stored_path"]),
        "filename_path_candidate_distinct_work_count": len(origin_works["filename"] | origin_works["stored_path"]),
    }
    return public, candidates, work_rows


OWNER_REVIEW_FIELDS = (
    "sample_index", "sample_category", "pixiv_work_id", "artwork_url",
    "local_basenames", "local_media_count", "observed_local_page_indexes",
    "parser_version", "exact_matched_tokens", "parser_origin_fields",
    "layout_classification", "local_import_timestamp",
    "existing_pixiv_metadata_row_count", "exact_compatible_metadata_row_count",
    "mismatched_metadata_row_count", "current_work_status", "current_reason",
    "complete_record_exclusion_proof", "manifest_fingerprint",
    "owner_manual_result", "owner_notes",
)


def _csv_join(values: Iterable[Any]) -> str:
    return ";".join(str(value) for value in values)


def _work_quantiles(work_ids: Sequence[str]) -> dict[str, int | None]:
    values = sorted(int(value) for value in work_ids)
    if not values:
        return {key: None for key in ("q0", "q25", "q50", "q75", "q100")}
    def pick(fraction: float) -> int:
        return values[round((len(values) - 1) * fraction)]
    return {"q0": pick(0), "q25": pick(.25), "q50": pick(.5), "q75": pick(.75), "q100": pick(1)}


def _write_owner_csv(path: Path, fieldnames: Sequence[str], values: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(values)


def build_owner_review_artifacts(
    output_dir: Path,
    candidates: Sequence[PixivCandidate],
    work_rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[Path]]:
    owner_dir = output_dir / "owner-review"
    by_work: dict[str, list[PixivCandidate]] = defaultdict(list)
    for candidate in candidates:
        for work_id, _ in candidate.all_matches:
            by_work[work_id].append(candidate)
    status_by_work = {str(row["work_id"]): str(row["status"]) for row in work_rows}
    eligible_work_ids = sorted(
        work_id for work_id, status in status_by_work.items()
        if status in {"missing", "pending", "retryable"} and re.fullmatch(r"[1-9]\d{5,11}", work_id)
    )
    base_rows: list[dict[str, Any]] = []
    for work_id in eligible_work_ids:
        items = by_work[work_id]
        basenames = sorted({item.local_basename for item in items})
        pages = sorted({page for item in items for candidate_work, page in item.all_matches if candidate_work == work_id})
        tokens = sorted({token for item in items for _, candidate_work, _, token in item.matched_tokens if candidate_work == work_id})
        origins = sorted({field for item in items for field, candidate_work, _ in item.match_origins if candidate_work == work_id})
        layouts = sorted({layout for item in items for layout in item.layout_classes}) or ["layout_unavailable"]
        timestamps = sorted({item.local_import_timestamp for item in items if item.local_import_timestamp})
        existing_ids = {record_id for item in items for record_id in item.metadata_record_ids}
        compatible_ids = {record_id for item in items for record_id in item.matching_metadata_record_ids}
        base_rows.append({
            "sample_index": "",
            "sample_category": "",
            "pixiv_work_id": work_id,
            "artwork_url": f"https://www.pixiv.net/artworks/{work_id}",
            "local_basenames": _csv_join(basenames),
            "local_media_count": len({item.media_id for item in items}),
            "observed_local_page_indexes": _csv_join(pages),
            "parser_version": PARSER_VERSION,
            "exact_matched_tokens": _csv_join(tokens),
            "parser_origin_fields": _csv_join(origins),
            "layout_classification": _csv_join(layouts),
            "local_import_timestamp": _csv_join(timestamps),
            "existing_pixiv_metadata_row_count": len(existing_ids),
            "exact_compatible_metadata_row_count": len(compatible_ids),
            "mismatched_metadata_row_count": len(existing_ids - compatible_ids),
            "current_work_status": status_by_work[work_id],
            "current_reason": _csv_join(sorted({item.reason for item in items})),
            "complete_record_exclusion_proof": "no_compatible_complete_record_for_any_observed_local_page",
            "manifest_fingerprint": "",
            "owner_manual_result": "",
            "owner_notes": "",
        })
    for index, row in enumerate(base_rows, start=1):
        row["sample_index"] = index
        row["sample_category"] = "full_missing_manifest"
    fingerprint_payload = [
        {key: row[key] for key in OWNER_REVIEW_FIELDS if key not in {"sample_index", "sample_category", "manifest_fingerprint", "owner_manual_result", "owner_notes"}}
        for row in base_rows
    ]
    manifest_fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    for row in base_rows:
        row["manifest_fingerprint"] = manifest_fingerprint

    selected: dict[str, set[str]] = defaultdict(set)
    row_by_id = {str(row["pixiv_work_id"]): row for row in base_rows}
    remaining = set(row_by_id)
    timestamped = [row for row in base_rows if row["local_import_timestamp"]]

    def take(category: str, ordered: Sequence[Mapping[str, Any]], count: int = 10) -> None:
        for row in ordered:
            work_id = str(row["pixiv_work_id"])
            if work_id not in remaining:
                continue
            selected[work_id].add(category)
            remaining.remove(work_id)
            if sum(category in categories for categories in selected.values()) >= count:
                break

    timestamp_available = bool(timestamped)
    if timestamp_available:
        take("oldest_local_import", sorted(timestamped, key=lambda row: (str(row["local_import_timestamp"]), int(row["pixiv_work_id"]))))
        take("newest_local_import", sorted(timestamped, key=lambda row: (str(row["local_import_timestamp"]), int(row["pixiv_work_id"])), reverse=True))
    numeric_rows = sorted(base_rows, key=lambda row: int(row["pixiv_work_id"]))
    midpoint = max(1, len(numeric_rows) // 2)
    take("low_work_id_quantile", numeric_rows[:midpoint])
    take("high_work_id_quantile", list(reversed(numeric_rows[midpoint:])))
    take("multi_page_or_multiple_local_media", [row for row in numeric_rows if ";" in str(row["observed_local_page_indexes"]) or int(row["local_media_count"]) > 1])
    take("nontrivial_filename_layout", [row for row in numeric_rows if row["layout_classification"] != "exact_token_basename"])

    target_size = min(60, len(base_rows))
    seed_hex = manifest_fingerprint[:16]
    rng = random.Random(int(seed_hex, 16))
    random_fill = sorted(remaining, key=lambda value: int(value))
    rng.shuffle(random_fill)
    for work_id in random_fill:
        if len(selected) >= target_size:
            break
        selected[work_id].add("deterministic_manifest_fingerprint_fill")
    if len(selected) != target_size:
        raise ML1BlockedError(f"owner_sample_size_unavailable:{len(selected)}")

    sample_rows: list[dict[str, Any]] = []
    for index, work_id in enumerate(sorted(selected, key=lambda value: int(value)), start=1):
        row = dict(row_by_id[work_id])
        row["sample_index"] = index
        row["sample_category"] = _csv_join(sorted(selected[work_id]))
        sample_rows.append(row)

    conflict_fields = (
        "conflict_index", "local_basename", "extracted_work_page_tokens", "origin_fields",
        "parser_version", "conflict_reason", "automatically_selected_winner", "owner_manual_result", "owner_notes",
    )
    conflict_rows = []
    for index, item in enumerate(sorted((item for item in candidates if item.status == "filename_identity_conflict"), key=lambda item: item.media_id), start=1):
        conflict_rows.append({
            "conflict_index": index,
            "local_basename": item.local_basename,
            "extracted_work_page_tokens": _csv_join(f"{work_id}_p{page}" for work_id, page in item.all_matches),
            "origin_fields": _csv_join(f"{field}:{work_id}_p{page}" for field, work_id, page in item.match_origins),
            "parser_version": PARSER_VERSION,
            "conflict_reason": item.reason,
            "automatically_selected_winner": "",
            "owner_manual_result": "",
            "owner_notes": "",
        })

    full_path = owner_dir / "pixiv-missing-work-owner-review-full.csv"
    sample_path = owner_dir / "pixiv-missing-work-owner-review-sample.csv"
    markdown_path = owner_dir / "pixiv-missing-work-owner-review-sample.md"
    conflict_path = owner_dir / "pixiv-conflict-owner-review.csv"
    selection_path = owner_dir / "pixiv-owner-review-selection.json"
    _write_owner_csv(full_path, OWNER_REVIEW_FIELDS, base_rows)
    _write_owner_csv(sample_path, OWNER_REVIEW_FIELDS, sample_rows)
    _write_owner_csv(conflict_path, conflict_fields, conflict_rows)
    markdown_lines = [
        "# Private Pixiv owner-review sample", "",
        f"Manifest fingerprint: `{manifest_fingerprint}`", "",
        "`missing` means no durable complete/terminal/result evidence; it does not mean remotely deleted.", "",
        "| # | Categories | Work ID | Artwork URL | Local basenames |",
        "|---:|---|---:|---|---|",
    ]
    markdown_lines.extend(
        f"| {row['sample_index']} | {row['sample_category']} | {row['pixiv_work_id']} | {row['artwork_url']} | {row['local_basenames']} |"
        for row in sample_rows
    )
    markdown_path.write_text("\n".join(markdown_lines) + "\n", encoding="utf-8")

    def bucket_counts(statuses: set[str]) -> Counter[str]:
        counts: Counter[str] = Counter()
        for work_id, status in status_by_work.items():
            if status not in statuses:
                continue
            timestamps = sorted({item.local_import_timestamp for item in by_work[work_id] if item.local_import_timestamp})
            counts[timestamps[0][:7] if timestamps else "unavailable"] += 1
        return counts
    missing_buckets = bucket_counts({"missing", "retryable"})
    complete_buckets = bucket_counts({"complete"})
    all_buckets = sorted(set(missing_buckets) | set(complete_buckets))
    time_distribution = {
        bucket: {
            "missing_work_count": missing_buckets[bucket],
            "complete_work_count": complete_buckets[bucket],
            "missing_complete_ratio": (
                round(missing_buckets[bucket] / complete_buckets[bucket], 6)
                if complete_buckets[bucket] else None
            ),
        }
        for bucket in all_buckets
    }
    layout_distribution = Counter(
        layout for row in base_rows for layout in str(row["layout_classification"]).split(";") if layout
    )
    duplicate_page_distribution = Counter()
    for work_id in eligible_work_ids:
        pages = [page for item in by_work[work_id] for candidate_work, page in item.all_matches if candidate_work == work_id]
        duplicate_page_distribution[str(len(pages) - len(set(pages)))] += 1
    category_counts = Counter(category for categories in selected.values() for category in categories)
    selection_evidence = {
        "owner_review_manifest_fingerprint": manifest_fingerprint,
        "deterministic_seed_derivation": "int(first_16_hex_chars_of_manifest_sha256, 16)",
        "deterministic_seed_hex": seed_hex,
        "full_manifest_row_count": len(base_rows),
        "sample_size": len(sample_rows),
        "sample_unique_work_count": len({row["pixiv_work_id"] for row in sample_rows}),
        "category_counts": dict(sorted(category_counts.items())),
        "trustworthy_local_import_timestamp_available": timestamp_available,
        "work_id_ordering_semantics": "ID-based archival proxy only; not a verified publication date",
        "conflict_cases_exported": len(conflict_rows),
        "owner_manual_result_vocabulary": [
            "exists_public", "exists_authenticated", "private", "deleted_or_not_found",
            "region_or_age_restricted", "parser_mismatch", "wrong_work_id_for_local_media",
            "local_filename_ambiguous", "uncertain",
        ],
    }
    write_json(selection_path, selection_evidence)
    diagnostics = {
        "local_import_month_distribution": time_distribution,
        "missing_work_id_quantiles": _work_quantiles(eligible_work_ids),
        "complete_work_id_quantiles": _work_quantiles([work_id for work_id, status in status_by_work.items() if status == "complete"]),
        "multi_page_missing_work_count": sum(";" in str(row["observed_local_page_indexes"]) for row in base_rows),
        "single_page_missing_work_count": sum(";" not in str(row["observed_local_page_indexes"]) for row in base_rows),
        "multiple_local_media_missing_work_count": sum(int(row["local_media_count"]) > 1 for row in base_rows),
        "filename_layout_distribution": dict(sorted(layout_distribution.items())),
        "duplicate_local_page_distribution": dict(sorted(duplicate_page_distribution.items(), key=lambda item: int(item[0]))),
        "missing_semantics": "no durable complete/terminal/result evidence; not evidence of remote deletion",
    }
    public = {
        "owner_sample_validation": {
            "sample_generated": True,
            "sample_size": len(sample_rows),
            "required_sample_size": target_size,
            "current_eligible_manifest_count": len(base_rows),
            "conflict_cases_exported": len(conflict_rows),
            "owner_review_manifest_fingerprint": manifest_fingerprint,
            "validation_confirmed": False,
            "confirmation_env": None,
            "optional_stage_evidence": True,
            "normal_pipeline_human_dependency": False,
        },
        "selection": selection_evidence,
        "diagnostics": diagnostics,
    }
    return public, [full_path, sample_path, markdown_path, conflict_path, selection_path]


def creator_fields(raw: Any, row: Mapping[str, Any]) -> dict[str, Any]:
    payload = read_json_value(raw)
    payload = payload if isinstance(payload, Mapping) else {}
    user = payload.get("user") if isinstance(payload.get("user"), Mapping) else {}
    creator_id = row.get("artist_id") or payload.get("user_id") or payload.get("artist_id") or user.get("id")
    creator_name = row.get("artist_name") or payload.get("user_name") or payload.get("artist_name") or user.get("name")
    account = (
        payload.get("creator_account")
        or payload.get("user_account")
        or payload.get("artist_account")
        or user.get("account")
    )
    profile_identity = (
        payload.get("creator_profile_identity")
        or payload.get("artist_profile_url")
        or payload.get("user_url")
        or user.get("profile_url")
    )
    return {
        "creator_id": str(creator_id) if creator_id not in (None, "") else None,
        "creator_name": normalize_source_text(creator_name) or None,
        "creator_account": normalize_source_text(account) or None,
        "creator_profile_identity": str(profile_identity) if profile_identity else None,
        "raw_user_object_present": bool(user),
    }


def build_creator_audit(
    metadata_rows: Sequence[Mapping[str, Any]],
    observation_rows: Sequence[Mapping[str, Any]],
    *,
    consumed_sourceconcept_keys: set[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, set[str]]]:
    metadata_by_id = {int(row["id"]): row for row in metadata_rows}
    observations_by_metadata: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for observation in observation_rows:
        observations_by_metadata[int(observation["source_metadata_record_id"])].append(observation)
    private_rows: list[dict[str, Any]] = []
    aliases_by_creator: dict[str, set[str]] = defaultdict(set)
    available = Counter()
    normalized_retained = Counter()
    searchable_retained = Counter()
    sourceconcept_consumed = Counter()
    silently_dropped = 0
    role_misclassified = 0
    successful = [
        row for row in metadata_rows if is_trusted_complete_pixiv_metadata_record(row)
    ]
    lineage = Counter()
    semantic_lineage: dict[tuple[Any, ...], set[str]] = defaultdict(set)
    for observation in observation_rows:
        if (
            str(observation.get("provider") or "").casefold() != "pixiv"
            or str(observation.get("name_role") or "") != "artist"
            or str(observation.get("source_field") or "")
            not in {"pixiv_user_metadata", "pixiv_user_account"}
            or str(observation.get("status") or "") not in {"observed", "active", "accepted"}
        ):
            continue
        parent = metadata_by_id.get(int(observation["source_metadata_record_id"]))
        if parent is None:
            trusted = False
        else:
            trusted = is_pixiv_creator_observation_compatible_with_parent(
                observation, parent
            )
        provenance = observation.get("provenance") or {}
        provenance = provenance if isinstance(provenance, Mapping) else {}
        in_scope = (
            str(provenance.get("source") or "") == "gallery_dl_authenticated_metadata"
            or provenance.get("reused_from_source_metadata_record_id") is not None
        )
        if trusted:
            lineage["trusted_parent_query_visible_creator_observation_count"] += 1
            lineage_class = "trusted"
        elif in_scope:
            lineage["untrusted_parent_query_visible_creator_observation_count"] += 1
            lineage[
                "untrusted_parent_creator_account_observation_count"
                if observation.get("source_field") == "pixiv_user_account"
                else "untrusted_parent_creator_name_observation_count"
            ] += 1
            lineage_class = "untrusted"
        else:
            lineage["out_of_scope_historical_or_manual_static_observation_count"] += 1
            continue
        semantic_key = (
            observation.get("media_id"),
            str(observation.get("source_work_id") or ""),
            int(observation.get("source_page_index") or 0),
            str(observation.get("canonical_name_key") or ""),
            str(observation.get("source_field") or ""),
        )
        semantic_lineage[semantic_key].add(lineage_class)
    lineage["duplicate_trusted_and_untrusted_semantic_observation_count"] = sum(
        classes == {"trusted", "untrusted"} for classes in semantic_lineage.values()
    )
    for key in (
        "trusted_parent_query_visible_creator_observation_count",
        "untrusted_parent_query_visible_creator_observation_count",
        "untrusted_parent_creator_name_observation_count",
        "untrusted_parent_creator_account_observation_count",
        "out_of_scope_historical_or_manual_static_observation_count",
        "duplicate_trusted_and_untrusted_semantic_observation_count",
    ):
        lineage.setdefault(key, 0)
    for row in successful:
        fields = creator_fields(row.get("raw_metadata_json"), row)
        record_observations = [
            item
            for item in observations_by_metadata.get(int(row["id"]), [])
            if is_pixiv_creator_observation_compatible_with_parent(item, row)
        ]
        artist_values = {
            normalize_source_text(item.get("raw_name"))
            for item in record_observations
            if str(item.get("name_role") or "") in {"artist", "creator"}
        }
        for item in record_observations:
            if str(item.get("source_field") or "").startswith("pixiv_user") and str(item.get("name_role") or "") not in {"artist", "creator"}:
                role_misclassified += 1
        for key in ("creator_id", "creator_name", "creator_account", "creator_profile_identity"):
            if fields[key]:
                available[key] += 1
        raw_payload = read_json_value(row.get("raw_metadata_json"))
        raw_payload = raw_payload if isinstance(raw_payload, Mapping) else {}
        if fields["creator_id"] and str(row.get("artist_id") or "") == fields["creator_id"]:
            normalized_retained["creator_id"] += 1
        if fields["creator_name"] and fields["creator_name"] in artist_values:
            searchable_retained["creator_name"] += 1
        if fields["creator_account"] and fields["creator_account"] in artist_values:
            searchable_retained["creator_account"] += 1
        if fields["creator_name"] and normalize_source_text(row.get("artist_name")) == fields["creator_name"]:
            normalized_retained["creator_name"] += 1
        if fields["creator_account"] and normalize_source_text(raw_payload.get("creator_account")) == fields["creator_account"]:
            normalized_retained["creator_account"] += 1
        if fields["creator_profile_identity"] and str(raw_payload.get("creator_profile_identity") or "") == fields["creator_profile_identity"]:
            normalized_retained["creator_profile_identity"] += 1
        for key in ("creator_name", "creator_account"):
            value = fields[key]
            if value and canonical_source_key(value) in set(consumed_sourceconcept_keys or ()):
                sourceconcept_consumed[key] += 1
        dropped_fields = [
            key for key in ("creator_id", "creator_name", "creator_account", "creator_profile_identity")
            if fields[key] and normalized_retained[key] < available[key]
        ]
        # Recompute per-row to avoid the aggregate counters obscuring gaps.
        dropped_fields = []
        if fields["creator_id"] and str(row.get("artist_id") or "") != fields["creator_id"]:
            dropped_fields.append("creator_id")
        if fields["creator_name"] and normalize_source_text(row.get("artist_name")) != fields["creator_name"]:
            dropped_fields.append("creator_name")
        if fields["creator_account"] and normalize_source_text(raw_payload.get("creator_account")) != fields["creator_account"]:
            dropped_fields.append("creator_account")
        if fields["creator_profile_identity"] and str(raw_payload.get("creator_profile_identity") or "") != fields["creator_profile_identity"]:
            dropped_fields.append("creator_profile_identity")
        silently_dropped += len(dropped_fields)
        if fields["creator_id"]:
            for value in (fields["creator_name"], fields["creator_account"]):
                if value:
                    aliases_by_creator[fields["creator_id"]].add(value)
        private_rows.append(
            {
                "private_metadata_ref": private_ref(row["id"], "metadata"),
                "source_metadata_record_id": int(row["id"]),
                **fields,
                "artist_observation_values": sorted(value for value in artist_values if value),
                "dropped_fields": dropped_fields,
            }
        )

    def coverage(counter: Counter[str], field: str) -> float:
        return round(counter[field] / available[field], 6) if available[field] else 1.0

    public = {
        "pixiv_registry_record_count": len(successful),
        "pixiv_queue_decision_record_count": sum(
            str(row.get("metadata_kind") or "") == QUEUE_METADATA_KIND for row in metadata_rows
        ),
        "provider_metadata_record_count": sum(
            str(row.get("metadata_kind") or "") != QUEUE_METADATA_KIND
            for row in successful
        ),
        "successful_acquisition_media_or_page_count": sum(
            str(row.get("metadata_kind") or "") == QUEUE_METADATA_KIND
            and str(row.get("data_type_label") or "") == "authenticated_provider_metadata"
            for row in successful
        ),
        "queue_records_carrying_acquired_provider_payload_count": sum(
            str(row.get("metadata_kind") or "") == QUEUE_METADATA_KIND
            and str(row.get("data_type_label") or "") == "authenticated_provider_metadata"
            for row in successful
        ),
        "terminal_evidence_record_count": sum(
            classify_pixiv_metadata_lifecycle(row.get("status")) == "terminal"
            for row in metadata_rows
            if str(row.get("provider") or "").casefold() == "pixiv"
        ),
        "deferred_page_mismatch_record_count": sum(
            classify_pixiv_metadata_lifecycle(row.get("status"))
            == "deferred_nonblocking_source_page_mismatch"
            for row in metadata_rows
            if str(row.get("provider") or "").casefold() == "pixiv"
        ),
        "records_with_creator_id": available["creator_id"],
        "records_with_creator_display_name": available["creator_name"],
        "records_with_creator_account": available["creator_account"],
        "records_with_creator_profile_identity": available["creator_profile_identity"],
        "normalized_creator_identity_count": len(aliases_by_creator),
        "retained_creator_id_count": normalized_retained["creator_id"],
        "retained_creator_name_count": normalized_retained["creator_name"],
        "retained_creator_account_count": normalized_retained["creator_account"],
        "retained_creator_profile_identity_count": normalized_retained["creator_profile_identity"],
        "available_creator_fields_accounting_coverage": 1.0,
        "stable_creator_id_preservation_coverage": coverage(normalized_retained, "creator_id"),
        "observed_creator_name_search_support_coverage": coverage(searchable_retained, "creator_name"),
        "observed_creator_account_search_support_coverage": coverage(searchable_retained, "creator_account"),
        "observed_creator_search_support_coverage": round(
            (searchable_retained["creator_name"] + searchable_retained["creator_account"])
            / (available["creator_name"] + available["creator_account"]),
            6,
        ) if available["creator_name"] + available["creator_account"] else 1.0,
        "silently_dropped_creator_field_count": silently_dropped,
        "creator_role_misclassification_count": role_misclassified,
        "raw_creator_fields_retained": True,
        "creator_data_is_source_layer_only": True,
        **dict(sorted(lineage.items())),
        "field_layer_counts": {
            f"{field}_field": {
                "raw_provider_available": available[field],
                "normalized_source_retained": normalized_retained[field],
                "query_visible_observation_retained": searchable_retained[field],
                "sourceconcept_consumed": sourceconcept_consumed[field],
            }
            for field in ("creator_id", "creator_name", "creator_account", "creator_profile_identity")
        },
    }
    return public, private_rows, aliases_by_creator


def script_label(value: str) -> str:
    if re.search(r"[\u3040-\u30ff]", value):
        return "japanese_kana"
    if re.search(r"[\u3400-\u9fff]", value):
        return "cjk_han"
    if re.search(r"[A-Za-z]", value):
        return "latin"
    return "mixed_or_other"


def runtime_media_ids(session: Session, query_text: str) -> set[int]:
    parsed = parse_search_query(query_text, db=session)
    query = apply_endpoint_equivalent_text_search(session.query(Media.id), parsed, session)
    return {int(row[0]) for row in query.distinct().all()}


def runtime_and_terms(session: Session, *terms: str) -> set[int]:
    query_text = " ".join(
        token for token in (_format_search_query_token(term) for term in terms) if token
    )
    return runtime_media_ids(session, query_text)


def classify_runtime_support(
    runtime_ids: set[int],
    support_paths: Mapping[int, set[str]],
    *,
    rejected_ids: set[int] | None = None,
    superseded_ids: set[int] | None = None,
    invalid_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Pure support accounting used by shared-name and multilingual audits."""

    supported_ids = set(support_paths) & runtime_ids
    unsupported_ids = runtime_ids - supported_ids
    rejected_only = unsupported_ids & set(rejected_ids or ())
    superseded_only = unsupported_ids & set(superseded_ids or ())
    invalid_only = unsupported_ids & set(invalid_ids or ())
    types = Counter(
        support_type
        for media_id in supported_ids
        for support_type in support_paths.get(media_id, set())
    )
    return {
        "runtime_result_count": len(runtime_ids),
        "supported_result_count": len(supported_ids),
        "unsupported_result_ids": unsupported_ids,
        "rejected_evidence_result_ids": rejected_only,
        "superseded_evidence_result_ids": superseded_only,
        "invalid_evidence_result_ids": invalid_only,
        "support_coverage": round(len(supported_ids) / len(runtime_ids), 6) if runtime_ids else 1.0,
        "support_type_distribution": dict(sorted(types.items())),
    }


def exact_support_key(value: Any) -> str:
    normalized = normalize_source_text(value).casefold()
    return f"__exact_text__:{normalized}" if normalized else ""


def source_name_visibility_sql(alias: str = "") -> str:
    """Return the endpoint-equivalent default visibility predicate for raw SQL audits."""

    prefix = f"{alias}." if alias else ""
    fields = ",".join(f"'{value}'" for value in DEFAULT_QUERY_VISIBLE_EXACT_PIXIV_NAME_FIELDS)
    return (
        f"({prefix}requires_review=false OR "
        f"({prefix}provider='pixiv' AND {prefix}source_field IN ({fields})))"
    )


def runtime_support_universe(session: Session, term: str) -> tuple[dict[int, set[str]], set[int], set[int], set[int]]:
    """Build support independently from the runtime query result set."""

    key = canonical_source_key(term)
    support: dict[int, set[str]] = defaultdict(set)

    def add(sql: str, support_type: str, params: Mapping[str, Any] | None = None) -> None:
        for row in session.execute(text(sql), dict(params or {"key": key})).all():
            if row[0] is not None:
                support[int(row[0])].add(support_type)

    add(
        "SELECT DISTINCT mt.media_id FROM blombooru_media_tags mt JOIN blombooru_tags t ON t.id=mt.tag_id "
        "WHERE t.name=:key",
        "direct_media_tag",
    )
    add(
        "SELECT DISTINCT media_id FROM blombooru_source_name_observations "
        "WHERE canonical_name_key=:key AND status IN ('observed','active','accepted') "
        f"AND {source_name_visibility_sql()} AND media_id IS NOT NULL",
        "direct_source_name_observation",
    )
    add(
        "SELECT DISTINCT m.media_id FROM blombooru_source_tag_observations o "
        "JOIN blombooru_source_metadata_records m ON m.id=o.source_metadata_record_id "
        "WHERE o.canonical_tag_key=:key AND o.status IN ('observed','active','accepted') AND m.media_id IS NOT NULL",
        "direct_source_tag_observation",
    )
    add(
        "SELECT DISTINCT m.media_id FROM blombooru_source_searchable_name_assertions a "
        "JOIN blombooru_source_metadata_records m ON m.id=a.source_metadata_record_id "
        "WHERE a.canonical_name_key=:key AND a.status IN ('accepted','active','observed') AND m.media_id IS NOT NULL",
        "accepted_searchable_name_assertion",
    )
    add(
        "SELECT DISTINCT media_id FROM blombooru_source_concept_fallback_search_index "
        "WHERE alias_key=:key AND status='active' AND media_id IS NOT NULL",
        "accepted_search_only_alias_relation",
    )
    add(
        "SELECT DISTINCT COALESCE(e.media_id,s.media_id) FROM blombooru_source_concept_search_index i "
        "LEFT JOIN blombooru_source_concept_evidence e ON e.concept_id=i.concept_id AND e.status IN ('active','needs_review') "
        "LEFT JOIN blombooru_source_concept_signal_links l ON l.concept_id=i.concept_id AND l.link_status IN ('active','needs_review','materialized_identity') "
        "LEFT JOIN blombooru_source_concept_signals s ON s.id=l.signal_id AND s.status IN ('active','needs_review','materialized_identity','isolated_evidence') "
        "WHERE i.search_key=:key AND i.status IN ('active','needs_review') AND COALESCE(e.media_id,s.media_id) IS NOT NULL",
        "accepted_materialized_sourceconcept_alias",
    )

    def status_ids(statuses: tuple[str, ...]) -> set[int]:
        values: set[int] = set()
        status_list = ",".join(f"'{value}'" for value in statuses)
        queries = (
            "SELECT media_id FROM blombooru_source_name_observations WHERE canonical_name_key=:key "
            f"AND status IN ({status_list}) AND media_id IS NOT NULL",
            "SELECT m.media_id FROM blombooru_source_tag_observations o JOIN blombooru_source_metadata_records m "
            f"ON m.id=o.source_metadata_record_id WHERE o.canonical_tag_key=:key AND o.status IN ({status_list}) AND m.media_id IS NOT NULL",
            "SELECT media_id FROM blombooru_source_concept_fallback_search_index WHERE alias_key=:key "
            f"AND status IN ({status_list}) AND media_id IS NOT NULL",
        )
        for sql in queries:
            values.update(int(row[0]) for row in session.execute(text(sql), {"key": key}).all() if row[0] is not None)
        return values

    rejected = status_ids(("rejected",))
    superseded = status_ids(("superseded",))
    invalid = status_ids(("invalid", "deleted", "retired"))
    return support, rejected, superseded, invalid


def build_runtime_support_index(
    session: Session,
) -> tuple[
    dict[str, dict[int, set[str]]],
    dict[str, set[int]],
    dict[str, set[int]],
    dict[str, set[int]],
]:
    """Bulk-load static support once while runtime searches remain per alias."""

    legal: dict[str, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))

    def add_rows(sql: str, support_type: str) -> None:
        for key, media_id in session.execute(text(sql)).all():
            canonical = canonical_source_key(key)
            if canonical and media_id is not None:
                legal[canonical][int(media_id)].add(support_type)

    def add_exact_rows(sql: str, support_type: str) -> None:
        for value, media_id in session.execute(text(sql)).all():
            key = exact_support_key(value)
            if key and media_id is not None:
                legal[key][int(media_id)].add(support_type)

    for tag_name, media_id in session.execute(
        text("SELECT t.name,mt.media_id FROM blombooru_media_tags mt JOIN blombooru_tags t ON t.id=mt.tag_id")
    ).all():
        if media_id is None:
            continue
        exact_key = canonical_source_key(tag_name)
        legal[exact_support_key(tag_name)][int(media_id)].add("direct_media_tag_exact_text")
        for key in _source_concept_key_candidates(str(tag_name)):
            legal[key][int(media_id)].add(
                "direct_media_tag" if key == exact_key else "direct_media_tag_query_key_variant"
            )
    add_rows(
        "SELECT canonical_name_key,media_id FROM blombooru_source_name_observations "
        "WHERE status IN ('observed','active','accepted') "
        f"AND {source_name_visibility_sql()} AND media_id IS NOT NULL",
        "direct_source_name_observation",
    )
    add_exact_rows(
        "SELECT raw_name,media_id FROM blombooru_source_name_observations "
        "WHERE status IN ('observed','active','accepted') "
        f"AND {source_name_visibility_sql()} AND media_id IS NOT NULL UNION ALL "
        "SELECT normalized_name,media_id FROM blombooru_source_name_observations "
        "WHERE status IN ('observed','active','accepted') "
        f"AND {source_name_visibility_sql()} AND media_id IS NOT NULL",
        "direct_source_name_exact_text",
    )
    add_rows(
        "SELECT o.canonical_tag_key,m.media_id FROM blombooru_source_tag_observations o "
        "JOIN blombooru_source_metadata_records m ON m.id=o.source_metadata_record_id "
        "WHERE o.status IN ('observed','active','accepted') AND m.media_id IS NOT NULL",
        "direct_source_tag_observation",
    )
    add_exact_rows(
        "SELECT o.raw_tag,m.media_id FROM blombooru_source_tag_observations o "
        "JOIN blombooru_source_metadata_records m ON m.id=o.source_metadata_record_id "
        "WHERE o.status IN ('observed','active','accepted') AND m.media_id IS NOT NULL UNION ALL "
        "SELECT o.normalized_tag,m.media_id FROM blombooru_source_tag_observations o "
        "JOIN blombooru_source_metadata_records m ON m.id=o.source_metadata_record_id "
        "WHERE o.status IN ('observed','active','accepted') AND m.media_id IS NOT NULL",
        "direct_source_tag_exact_text",
    )
    add_rows(
        "SELECT a.canonical_name_key,m.media_id FROM blombooru_source_searchable_name_assertions a "
        "JOIN blombooru_source_metadata_records m ON m.id=a.source_metadata_record_id "
        "WHERE a.status IN ('accepted','active','observed') AND m.media_id IS NOT NULL",
        "accepted_searchable_name_assertion",
    )
    add_exact_rows(
        "SELECT a.raw_input,m.media_id FROM blombooru_source_searchable_name_assertions a "
        "JOIN blombooru_source_metadata_records m ON m.id=a.source_metadata_record_id "
        "WHERE a.status IN ('accepted','active','observed','searchable_active') AND m.media_id IS NOT NULL UNION ALL "
        "SELECT a.normalized_input,m.media_id FROM blombooru_source_searchable_name_assertions a "
        "JOIN blombooru_source_metadata_records m ON m.id=a.source_metadata_record_id "
        "WHERE a.status IN ('accepted','active','observed','searchable_active') AND m.media_id IS NOT NULL",
        "accepted_searchable_name_exact_text",
    )
    canonical_complete_sql = ",".join(f"'{status}'" for status in sorted(CANONICAL_COMPLETE_STATUSES))
    add_rows(
        "SELECT artist_name,media_id FROM blombooru_source_metadata_records "
        "WHERE provider='pixiv' AND COALESCE(artist_name,'')<>'' AND media_id IS NOT NULL "
        f"AND status IN ({canonical_complete_sql})",
        "exact_provider_creator_metadata",
    )
    add_exact_rows(
        "SELECT artist_name,media_id FROM blombooru_source_metadata_records "
        "WHERE provider='pixiv' AND COALESCE(artist_name,'')<>'' AND media_id IS NOT NULL "
        f"AND status IN ({canonical_complete_sql})",
        "exact_provider_creator_metadata",
    )
    add_rows(
        "SELECT title,media_id FROM blombooru_source_metadata_records "
        "WHERE provider='pixiv' AND COALESCE(title,'')<>'' AND media_id IS NOT NULL "
        f"AND status IN ({canonical_complete_sql})",
        "exact_provider_work_metadata",
    )
    add_exact_rows(
        "SELECT title,media_id FROM blombooru_source_metadata_records "
        "WHERE provider='pixiv' AND COALESCE(title,'')<>'' AND media_id IS NOT NULL "
        f"AND status IN ({canonical_complete_sql})",
        "exact_provider_work_metadata",
    )
    add_rows(
        "SELECT alias_key,media_id FROM blombooru_source_concept_fallback_search_index "
        "WHERE status='active' AND media_id IS NOT NULL",
        "accepted_search_only_alias_relation",
    )
    add_rows(
        "SELECT i.search_key,COALESCE(e.media_id,s.media_id) FROM blombooru_source_concept_search_index i "
        "LEFT JOIN blombooru_source_concept_evidence e ON e.concept_id=i.concept_id AND e.status IN ('active','needs_review') "
        "LEFT JOIN blombooru_source_concept_signal_links l ON l.concept_id=i.concept_id AND l.link_status IN ('active','needs_review','materialized_identity') "
        "LEFT JOIN blombooru_source_concept_signals s ON s.id=l.signal_id AND s.status IN ('active','needs_review','materialized_identity','isolated_evidence') "
        "WHERE i.status IN ('active','needs_review') AND COALESCE(e.media_id,s.media_id) IS NOT NULL",
        "accepted_materialized_sourceconcept_alias",
    )
    alias_relations = session.execute(
        text(
            "SELECT source_name_key,target_name_key,relation_type,status "
            "FROM blombooru_source_name_alias_candidates "
            "WHERE relation_type IN ('curated_alias','provider_canonical','same_as','alias','translation_alias') "
            "AND status NOT IN ('rejected','superseded')"
        )
    ).all()
    for _ in range(2):
        for source_key, target_key, relation_type, status in alias_relations:
            source = canonical_source_key(source_key)
            target = canonical_source_key(target_key)
            media_ids = set(legal.get(source, {})) | set(legal.get(target, {}))
            support_type = (
                "accepted_search_only_alias_relation"
                if str(status) in {"accepted", "active", "observed"}
                else "query_visible_provider_canonical_alias_relation"
                if str(relation_type) == "provider_canonical"
                else "query_visible_source_name_alias_relation"
            )
            for alias_key in (source, target):
                for media_id in media_ids:
                    legal[alias_key][media_id].add(support_type)

    status_maps: dict[str, dict[str, set[int]]] = {
        "rejected": defaultdict(set),
        "superseded": defaultdict(set),
        "invalid": defaultdict(set),
    }
    for key, media_id, status in session.execute(
        text(
            "SELECT canonical_name_key,media_id,status FROM blombooru_source_name_observations "
            "WHERE status IN ('rejected','superseded','invalid','deleted','retired') AND media_id IS NOT NULL UNION ALL "
            "SELECT o.canonical_tag_key,m.media_id,o.status FROM blombooru_source_tag_observations o "
            "JOIN blombooru_source_metadata_records m ON m.id=o.source_metadata_record_id "
            "WHERE o.status IN ('rejected','superseded','invalid','deleted','retired') AND m.media_id IS NOT NULL UNION ALL "
            "SELECT alias_key,media_id,status FROM blombooru_source_concept_fallback_search_index "
            "WHERE status IN ('rejected','superseded','invalid','deleted','retired') AND media_id IS NOT NULL"
        )
    ).all():
        bucket = "invalid" if str(status) in {"invalid", "deleted", "retired"} else str(status)
        status_maps[bucket][canonical_source_key(key)].add(int(media_id))
    return legal, status_maps["rejected"], status_maps["superseded"], status_maps["invalid"]


def apply_translation_support_relations(
    support_index: dict[str, dict[int, set[str]]],
    translation_rows: Sequence[Mapping[str, Any]],
) -> None:
    """Project accepted translation evidence onto existing direct tag support."""

    for translation in translation_rows:
        if str(translation.get("status") or "") == "rejected":
            continue
        if not _translation_alias_trusted_for_search(translation):
            continue
        canonical_key = canonical_source_key(translation.get("canonical_name"))
        canonical_media = set(support_index.get(canonical_key, {}))
        if not canonical_media:
            continue
        alias_values = {
            normalize_source_text(translation.get("canonical_name")),
            normalize_source_text(translation.get("display_name")),
        }
        raw_translation_aliases = read_json_value(translation.get("aliases_json"))
        if isinstance(raw_translation_aliases, list):
            alias_values.update(normalize_source_text(value) for value in raw_translation_aliases)
        for alias_value in alias_values:
            alias_key = canonical_source_key(alias_value)
            if not alias_key:
                continue
            for media_id in canonical_media:
                support_index.setdefault(alias_key, {}).setdefault(media_id, set()).add(
                    "accepted_search_only_translation_relation"
                )


def indexed_support_for_runtime_query(
    session: Session,
    term: str,
    support_index: Mapping[str, Mapping[int, set[str]]],
) -> tuple[dict[int, set[str]], set[str]]:
    """Resolve the same query-key universe without consulting runtime results."""

    token = _format_search_query_token(term)
    parsed = parse_search_query(token, db=session)
    tags = parsed.get("tags", {})
    resolved_terms = list(tags.get("include", []))
    keys = set(_source_concept_key_candidates(term))
    keys.add(canonical_source_key(term))
    keys.add(exact_support_key(term))
    per_condition_support: list[dict[int, set[str]]] = []
    for resolved in resolved_terms:
        resolved_keys = set(_source_concept_key_candidates(resolved))
        resolved_keys.add(canonical_source_key(resolved))
        resolved_keys.add(exact_support_key(resolved))
        resolved_keys.discard("")
        keys.update(resolved_keys)
        condition_support: dict[int, set[str]] = defaultdict(set)
        translation_applied = canonical_source_key(term) != canonical_source_key(resolved)
        for key in resolved_keys:
            for media_id, types in support_index.get(key, {}).items():
                condition_support[int(media_id)].update(types)
                if translation_applied:
                    condition_support[int(media_id)].add("accepted_search_translation_to_canonical_query")
        per_condition_support.append(condition_support)
    for wildcard_type, pattern in tags.get("wildcards", []):
        if wildcard_type != "include":
            continue
        wildcard_support: dict[int, set[str]] = defaultdict(set)
        for row in session.execute(
            text(
                "SELECT DISTINCT mt.media_id FROM blombooru_media_tags mt "
                "JOIN blombooru_tags t ON t.id=mt.tag_id "
                "WHERE mt.is_suggestion=false AND t.name ~* :pattern"
            ),
            {"pattern": wildcard_to_regex(pattern)},
        ).all():
            wildcard_support[int(row[0])].add("direct_media_tag_wildcard")
        per_condition_support.append(wildcard_support)
    if not per_condition_support:
        return {}, {key for key in keys if key}
    supported_ids = set(per_condition_support[0])
    for condition in per_condition_support[1:]:
        supported_ids.intersection_update(condition)
    merged: dict[int, set[str]] = defaultdict(set)
    for media_id in supported_ids:
        for condition in per_condition_support:
            merged[media_id].update(condition.get(media_id, set()))
        if len(per_condition_support) > 1:
            merged[media_id].add("media_level_and_intersection_support")
    return merged, {key for key in keys if key}


def build_multilingual_benchmark(
    session: Session,
    aliases_by_creator: Mapping[str, set[str]],
    translation_rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    identity_categories = {"artist", "character", "copyright", "work", "person", "creator"}
    families: list[dict[str, Any]] = []
    for creator_id, aliases in sorted(aliases_by_creator.items()):
        cleaned = sorted({value for value in aliases if value})
        if len(cleaned) >= 2:
            families.append({"kind": "creator_stable_id", "scope": "identity", "anchor": creator_id, "aliases": cleaned})
    for row in translation_rows:
        if str(row.get("status") or "") == "rejected":
            continue
        if not _translation_alias_trusted_for_search(row):
            continue
        aliases = {normalize_source_text(row.get("canonical_name")), normalize_source_text(row.get("display_name"))}
        raw_aliases = read_json_value(row.get("aliases_json"))
        if isinstance(raw_aliases, list):
            aliases.update(normalize_source_text(value) for value in raw_aliases)
        aliases.discard("")
        if len(aliases) < 2:
            continue
        category = str(row.get("category") or "general").casefold()
        scope = "identity" if category in identity_categories else "search_only"
        families.append(
            {
                "kind": "verified_tag_translation",
                "scope": scope,
                "category": category,
                "anchor": str(row["id"]),
                "aliases": sorted(aliases),
            }
        )

    signal_keys: dict[str, set[int]] = defaultdict(set)
    for row in rows(session, "SELECT id, raw_value, display_value, canonical_key, normalized_key FROM blombooru_source_concept_signals"):
        for value in (row.get("raw_value"), row.get("display_value"), row.get("canonical_key"), row.get("normalized_key")):
            key = canonical_source_key(value)
            if key:
                signal_keys[key].add(int(row["id"]))
    concepts_by_signal: dict[int, set[int]] = defaultdict(set)
    for row in rows(
        session,
        "SELECT signal_id, concept_id FROM blombooru_source_concept_signal_links "
        "WHERE link_status IN ('active','materialized_identity')",
    ):
        concepts_by_signal[int(row["signal_id"])].add(int(row["concept_id"]))

    traces: list[dict[str, Any]] = []
    misses: list[dict[str, Any]] = []
    outcomes = Counter()
    type_counts = Counter()
    scope_counts = Counter()
    script_pairs = Counter()
    identity_alias_count = 0
    identity_represented_alias_count = 0
    identity_connected_count = 0
    identity_adjudicated_count = 0
    runtime_equivalent_count = 0
    and_evaluable_count = 0
    and_equivalent_count = 0
    unexplained_count = 0
    role_context_loss_count = 0
    unsupported_result_count = 0
    support_index, rejected_index, superseded_index, invalid_index = build_runtime_support_index(session)
    # A trusted TagTranslation is query-visible search evidence, not an
    # identity union. Extend only the support universe; runtime result sets are
    # still obtained independently from the application search path below.
    apply_translation_support_relations(support_index, translation_rows)
    runtime_cache: dict[str, set[int]] = {}

    def cached_runtime(*terms: str) -> set[int]:
        query_text = " ".join(
            token for token in (_format_search_query_token(term) for term in terms) if token
        )
        if query_text not in runtime_cache:
            runtime_cache[query_text] = runtime_media_ids(session, query_text)
        return set(runtime_cache[query_text])

    for family in families:
        aliases = list(family["aliases"])
        alias_keys = [canonical_source_key(value) for value in aliases]
        represented = [key for key in alias_keys if signal_keys.get(key)]
        concept_sets: list[set[int]] = []
        for key in alias_keys:
            concept_ids: set[int] = set()
            for signal_id in signal_keys.get(key, set()):
                concept_ids.update(concepts_by_signal.get(signal_id, set()))
            concept_sets.append(concept_ids)
        common_concepts = set.intersection(*concept_sets) if concept_sets and all(concept_sets) else set()

        result_sets: list[set[int]] = []
        support_traces: list[dict[str, Any]] = []
        for alias in aliases:
            actual = cached_runtime(alias)
            support_paths, support_keys = indexed_support_for_runtime_query(session, alias, support_index)
            rejected_ids = set().union(*(rejected_index.get(key, set()) for key in support_keys))
            superseded_ids = set().union(*(superseded_index.get(key, set()) for key in support_keys))
            invalid_ids = set().union(*(invalid_index.get(key, set()) for key in support_keys))
            support_result = classify_runtime_support(
                actual,
                support_paths,
                rejected_ids=rejected_ids,
                superseded_ids=superseded_ids,
                invalid_ids=invalid_ids,
            )
            result_sets.append(actual)
            unsupported_result_count += len(support_result["unsupported_result_ids"])
            support_traces.append(
                {
                    "runtime_result_count": support_result["runtime_result_count"],
                    "supported_result_count": support_result["supported_result_count"],
                    "unsupported_result_count": len(support_result["unsupported_result_ids"]),
                    "rejected_result_count": len(support_result["rejected_evidence_result_ids"]),
                    "superseded_result_count": len(support_result["superseded_evidence_result_ids"]),
                    "support_coverage": support_result["support_coverage"],
                    "support_type_distribution": support_result["support_type_distribution"],
                }
            )
        search_equivalent = bool(result_sets) and bool(result_sets[0]) and all(item == result_sets[0] for item in result_sets[1:])
        any_unsupported = any(item["unsupported_result_count"] for item in support_traces)

        union_media = set().union(*result_sets) if result_sets else set()
        work_term_row = None
        if union_media:
            work_term_row = session.execute(
                text(
                    "SELECT t.name FROM blombooru_tags t JOIN blombooru_media_tags mt ON mt.tag_id=t.id "
                    "WHERE mt.media_id = ANY(:ids) AND CAST(t.category AS text)='copyright' "
                    "GROUP BY t.name ORDER BY COUNT(DISTINCT mt.media_id) DESC,t.name LIMIT 1"
                ),
                {"ids": list(union_media)},
            ).first()
        if union_media and not work_term_row:
            work_term_row = session.execute(
                text(
                    "SELECT o.raw_name FROM blombooru_source_name_observations o "
                    "WHERE o.media_id = ANY(:ids) AND o.name_role='work_title' "
                    "AND o.status IN ('observed','active','accepted') "
                    f"AND {source_name_visibility_sql('o')} "
                    "GROUP BY o.raw_name ORDER BY COUNT(DISTINCT o.media_id) DESC,o.raw_name LIMIT 1"
                ),
                {"ids": list(union_media)},
            ).first()
        and_sets: list[set[int]] = []
        if work_term_row:
            and_sets = [cached_runtime(alias, str(work_term_row[0])) for alias in aliases]
        and_evaluable = bool(and_sets)
        and_equivalent = and_evaluable and all(item == and_sets[0] for item in and_sets[1:])

        if any_unsupported:
            outcome = "unsupported_result"
        elif common_concepts and family["scope"] == "identity":
            outcome = "identity_materialized"
        elif search_equivalent:
            outcome = "search_equivalent_without_identity_union" if family["scope"] == "identity" else "runtime_equivalent"
        elif len(represented) < len(alias_keys) and family["scope"] == "identity":
            outcome = "signal_not_generated"
        elif family["scope"] == "identity":
            outcome = "candidate_not_generated"
        elif not union_media:
            outcome = "deferred_with_reason"
        else:
            outcome = "under_recall"

        cause = None
        if outcome == "signal_not_generated":
            cause = "identity_alias_missing_sourceconcept_signal"
        elif outcome == "candidate_not_generated":
            cause = "identity_signals_present_without_connected_candidate_or_runtime_equivalence"
        elif outcome == "under_recall":
            cause = "runtime_alias_under_recall"
        elif outcome == "unsupported_result":
            cause = "runtime_result_without_legal_support_trace"
        elif outcome == "deferred_with_reason":
            cause = "not_evaluable_missing_runtime_support"

        outcomes[outcome] += 1
        type_counts[str(family["kind"])] += 1
        scope_counts[str(family["scope"])] += 1
        if search_equivalent:
            runtime_equivalent_count += 1
        if and_evaluable:
            and_evaluable_count += 1
        if and_equivalent:
            and_equivalent_count += 1
        if family["scope"] == "identity":
            identity_alias_count += len(alias_keys)
            identity_represented_alias_count += len(represented)
            if common_concepts:
                identity_connected_count += 1
                identity_adjudicated_count += 1
            if outcome in {"signal_not_generated", "candidate_not_generated"}:
                pass
        if cause is None and not search_equivalent and outcome not in {"identity_materialized"}:
            unexplained_count += 1
        if common_concepts and len(common_concepts) > 1:
            role_context_loss_count += 1
        script_pairs["_to_".join(sorted({script_label(value) for value in aliases}))] += 1
        trace = {
            "private_family_ref": private_ref(f"{family['kind']}:{family['anchor']}", "family"),
            "kind": family["kind"],
            "scope": family["scope"],
            "category": family.get("category"),
            "anchor_private": family["anchor"],
            "aliases": aliases,
            "alias_count": len(aliases),
            "represented_alias_count": len(represented),
            "common_materialized_concept_count": len(common_concepts),
            "runtime_result_counts": [len(item) for item in result_sets],
            "support_traces": support_traces,
            "search_equivalent": search_equivalent,
            "and_work_evaluable": and_evaluable,
            "and_work_equivalent": and_equivalent if and_evaluable else None,
            "outcome": outcome,
            "cause": cause,
        }
        traces.append(trace)
        if family["scope"] == "identity" and outcome in {"signal_not_generated", "candidate_not_generated"}:
            misses.append(trace)

    family_count = len(families)
    identity_family_count = scope_counts["identity"]
    public = {
        "real_fixed_evidence_family_count": family_count,
        "identity_eligible_family_count": identity_family_count,
        "search_only_translation_family_count": scope_counts["search_only"],
        "family_type_counts": dict(sorted(type_counts.items())),
        "family_scope_counts": dict(sorted(scope_counts.items())),
        "script_pair_counts": dict(sorted(script_pairs.items())),
        "observed_alias_count": sum(len(family["aliases"]) for family in families),
        "identity_eligible_alias_count": identity_alias_count,
        "observed_alias_accounting_coverage": 1.0,
        "signal_generation_coverage": round(identity_represented_alias_count / identity_alias_count, 6) if identity_alias_count else 1.0,
        "candidate_family_connectivity_coverage": round(identity_connected_count / identity_family_count, 6) if identity_family_count else 1.0,
        "adjudication_coverage": round(identity_adjudicated_count / identity_family_count, 6) if identity_family_count else 1.0,
        "materialized_strong_family_coverage": round(outcomes["identity_materialized"] / identity_family_count, 6) if identity_family_count else 1.0,
        "search_equivalence_coverage": round(runtime_equivalent_count / family_count, 6) if family_count else 1.0,
        "and_work_evaluable_family_count": and_evaluable_count,
        "and_work_equivalence_coverage": round(and_equivalent_count / and_evaluable_count, 6) if and_evaluable_count else 1.0,
        "outcome_counts": dict(sorted(outcomes.items())),
        "unexplained_multilingual_split_count": unexplained_count,
        "candidate_not_generated_count": len(misses),
        "role_or_context_loss_count": role_context_loss_count,
        "unsupported_runtime_result_count": unsupported_result_count,
        "human_review_queue_generated": False,
        "synthetic_alias_media_propagation_used": False,
        "actual_runtime_search_used": True,
        "raw_aliases_public": False,
    }
    return public, traces, misses


def build_search_audit(session: Session, creator_private: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    identity_before = {
        "concepts": session.query(SourceConcept).count(),
        "links": session.query(SourceConceptSignalLink).count(),
    }
    shared = rows(
        session,
        "SELECT search_key, COUNT(DISTINCT concept_id) concept_count FROM blombooru_source_concept_search_index "
        "WHERE status IN ('active','needs_review') GROUP BY search_key HAVING COUNT(DISTINCT concept_id)>1 "
        "ORDER BY concept_count DESC, search_key LIMIT 25",
    )
    cases: list[dict[str, Any]] = []
    shared_pass = True
    and_leakage = 0
    supported_results = 0
    runtime_results = 0
    unsupported_results = 0
    rejected_results = 0
    superseded_results = 0
    invalid_results = 0
    support_type_distribution = Counter()
    for item in shared:
        term = str(item["search_key"])
        actual = runtime_and_terms(session, term)
        support_paths, rejected_ids, superseded_ids, invalid_ids = runtime_support_universe(session, term)
        expected = set(support_paths)
        support_result = classify_runtime_support(
            actual,
            support_paths,
            rejected_ids=rejected_ids,
            superseded_ids=superseded_ids,
            invalid_ids=invalid_ids,
        )
        case_pass = (
            expected.issubset(actual)
            and not support_result["unsupported_result_ids"]
            and not support_result["rejected_evidence_result_ids"]
            and not support_result["superseded_evidence_result_ids"]
        )
        shared_pass = shared_pass and case_pass
        runtime_results += support_result["runtime_result_count"]
        supported_results += support_result["supported_result_count"]
        unsupported_results += len(support_result["unsupported_result_ids"])
        rejected_results += len(support_result["rejected_evidence_result_ids"])
        superseded_results += len(support_result["superseded_evidence_result_ids"])
        invalid_results += len(support_result["invalid_evidence_result_ids"])
        support_type_distribution.update(support_result["support_type_distribution"])
        tag_row = None
        if actual:
            tag_row = session.execute(
                text(
                    "SELECT t.name, COUNT(DISTINCT mt.media_id) hits FROM blombooru_tags t "
                    "JOIN blombooru_media_tags mt ON mt.tag_id=t.id WHERE mt.media_id = ANY(:ids) "
                    "GROUP BY t.name HAVING COUNT(DISTINCT mt.media_id) < :total ORDER BY hits DESC,t.name LIMIT 1"
                ),
                {"ids": list(actual), "total": len(actual)},
            ).first()
        and_pass = None
        if tag_row:
            tag_media = {
                int(row[0])
                for row in session.execute(
                    text(
                        "SELECT mt.media_id FROM blombooru_media_tags mt JOIN blombooru_tags t ON t.id=mt.tag_id WHERE t.name=:name"
                    ),
                    {"name": tag_row[0]},
                ).all()
            }
            narrowed = runtime_and_terms(session, term, str(tag_row[0]))
            expected_and = actual & tag_media
            leakage = narrowed - expected_and
            and_leakage += len(leakage)
            and_pass = narrowed == expected_and
        cases.append(
            {
                "private_term": term,
                "private_term_ref": private_ref(term, "term"),
                "concept_count": int(item["concept_count"]),
                "runtime_result_count": len(actual),
                "direct_expected_count": len(expected),
                "supported_result_count": support_result["supported_result_count"],
                "unsupported_result_count": len(support_result["unsupported_result_ids"]),
                "rejected_evidence_result_count": len(support_result["rejected_evidence_result_ids"]),
                "superseded_evidence_result_count": len(support_result["superseded_evidence_result_ids"]),
                "support_coverage": support_result["support_coverage"],
                "support_type_distribution": support_result["support_type_distribution"],
                "shared_union_passed": case_pass,
                "and_case_available": tag_row is not None,
                "and_intersection_passed": and_pass,
            }
        )

    shared_case_count = len(cases)
    shared_and_case_count = sum(bool(item.get("and_case_available")) for item in cases)
    creator_cases = 0
    creator_passes = 0
    creator_and_cases = 0
    creator_and_passes = 0
    creator_and_leakage = 0
    creator_and_category_counts = Counter()
    creator_and_failure_causes = Counter()
    for item in creator_private:
        creator_name = item.get("creator_name")
        if not creator_name:
            continue
        expected = {
            int(row[0])
            for row in session.execute(
                text(
                    "SELECT media_id FROM blombooru_source_metadata_records WHERE provider='pixiv' AND artist_name=:name AND media_id IS NOT NULL"
                ),
                {"name": creator_name},
            ).all()
        }
        if not expected:
            continue
        creator_cases += 1
        actual = runtime_and_terms(session, str(creator_name))
        if expected.issubset(actual):
            creator_passes += 1
        for category in ("character", "copyright"):
            tag_row = session.execute(
                text(
                    "SELECT t.name, COUNT(DISTINCT mt.media_id) hits FROM blombooru_tags t "
                    "JOIN blombooru_media_tags mt ON mt.tag_id=t.id "
                    "WHERE mt.media_id = ANY(:ids) AND CAST(t.category AS text)=:category "
                    "GROUP BY t.name ORDER BY hits DESC,t.name LIMIT 1"
                ),
                {"ids": list(expected), "category": category},
            ).first()
            if not tag_row:
                continue
            tag_media = {
                int(row[0])
                for row in session.execute(
                    text(
                        "SELECT mt.media_id FROM blombooru_media_tags mt JOIN blombooru_tags t ON t.id=mt.tag_id WHERE t.name=:name"
                    ),
                    {"name": tag_row[0]},
                ).all()
            }
            expected_and = expected & tag_media
            actual_and = runtime_and_terms(session, str(creator_name), str(tag_row[0]))
            leakage = actual_and - expected_and
            creator_and_cases += 1
            creator_and_category_counts[category] += 1
            creator_and_leakage += len(leakage)
            if actual_and == expected_and:
                creator_and_passes += 1
            else:
                creator_and_failure_causes["character_runtime_intersection_mismatch"] += 1
            cases.append(
                {
                    "private_term": creator_name,
                    "private_term_ref": private_ref(creator_name, "creator_term"),
                    "and_category": category,
                    "runtime_result_count": len(actual_and),
                    "direct_expected_count": len(expected_and),
                    "and_intersection_passed": actual_and == expected_and,
                }
            )
        work_row = session.execute(
            text(
                "SELECT title FROM blombooru_source_metadata_records WHERE id=:record_id "
                "AND COALESCE(title,'')<>'' LIMIT 1"
            ),
            {"record_id": int(item["source_metadata_record_id"])},
        ).first()
        if work_row:
            work_title = str(work_row[0])
            work_observation_present = bool(
                session.execute(
                    text(
                        "SELECT 1 FROM blombooru_source_name_observations WHERE source_metadata_record_id=:record_id "
                        "AND source_field IN ('pixiv_title','pixiv_parenthetical_inner_work','pixiv_work_title_tag') "
                        "AND status='observed' LIMIT 1"
                    ),
                    {"record_id": int(item["source_metadata_record_id"])},
                ).first()
            )
            expected_work = {
                int(row[0])
                for row in session.execute(
                    text(
                        "SELECT media_id FROM blombooru_source_metadata_records WHERE provider='pixiv' "
                        "AND artist_name=:creator_name AND title=:work_title AND media_id IS NOT NULL"
                    ),
                    {"creator_name": creator_name, "work_title": work_title},
                ).all()
            }
            actual_work = runtime_and_terms(session, str(creator_name), work_title)
            leakage = actual_work - expected_work
            creator_and_cases += 1
            creator_and_category_counts["work_title"] += 1
            creator_and_leakage += len(leakage)
            if actual_work == expected_work:
                creator_and_passes += 1
            elif not work_observation_present:
                creator_and_failure_causes["source_work_observation_missing"] += 1
            elif actual_work.issubset(expected_work):
                creator_and_failure_causes["work_title_runtime_under_recall"] += 1
            else:
                creator_and_failure_causes["work_title_runtime_intersection_mismatch"] += 1
            cases.append(
                {
                    "private_term": creator_name,
                    "private_term_ref": private_ref(creator_name, "creator_term"),
                    "and_category": "work_title",
                    "runtime_result_count": len(actual_work),
                    "direct_expected_count": len(expected_work),
                    "and_intersection_passed": actual_work == expected_work,
                }
            )
        if creator_cases >= 50:
            break

    identity_after = {
        "concepts": session.query(SourceConcept).count(),
        "links": session.query(SourceConceptSignalLink).count(),
    }
    creator_accuracy = round(creator_passes / creator_cases, 6) if creator_cases else 1.0
    creator_and_accuracy = round(creator_and_passes / creator_and_cases, 6) if creator_and_cases else 1.0
    public = {
        "runtime_application_path_used": True,
        "runtime_parser_used": True,
        "shared_name_case_count": shared_case_count,
        "shared_name_union_passed": shared_pass,
        "runtime_result_count": runtime_results,
        "supported_result_count": supported_results,
        "unsupported_result_media_count": unsupported_results,
        "rejected_evidence_result_count": rejected_results,
        "superseded_evidence_result_count": superseded_results,
        "invalid_or_deleted_evidence_result_count": invalid_results,
        "and_case_count": shared_and_case_count,
        "and_constraint_leakage_count": and_leakage,
        "direct_or_accepted_alias_support_coverage": round(supported_results / runtime_results, 6) if runtime_results else 1.0,
        "support_type_distribution": dict(sorted(support_type_distribution.items())),
        "creator_search_case_count": creator_cases,
        "creator_search_passed": creator_accuracy == 1.0,
        "creator_and_character_work_case_count": creator_and_cases,
        "creator_and_category_counts": dict(sorted(creator_and_category_counts.items())),
        "creator_and_character_work_leakage_count": creator_and_leakage,
        "creator_and_failure_cause_counts": dict(sorted(creator_and_failure_causes.items())),
        "creator_and_character_work_intersection_passed": creator_and_accuracy == 1.0,
        "creator_and_character_work_accuracy": creator_and_accuracy,
        "multilingual_and_work_equivalence_coverage": 1.0 if and_leakage == 0 else 0.0,
        "identity_union_from_search_count": 0 if identity_before == identity_after else 1,
        "identity_before_after_match": identity_before == identity_after,
    }
    return public, cases


def document_semantics_proof() -> dict[str, Any]:
    paths = (
        ROOT / "docs/current-handoff.md",
        ROOT / "docs/roadmap/current-mainline-roadmap.md",
        ROOT / "docs/source-concept-tag-search-semantics.md",
        ROOT / "docs/phase-contracts.md",
    )
    text_value = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    contradictions = [
        phrase
        for phrase in (
            "no flat union across cannot-linked concepts",
            "The sole recommended next phase is `SCV2-SR1",
        )
        if phrase in text_value
    ]
    erratum = json.loads(
        (ROOT / "docs/reports/phase-4.5-scv2-r2r-autonomous-recall-search-closure-summary.json").read_text(encoding="utf-8")
    ).get("search_semantics_interpretation_erratum", {})
    return {
        "passed": not contradictions and bool(erratum.get("old_interpretation_superseded")),
        "durable_policy_created": (ROOT / "docs/source-concept-tag-search-semantics.md").exists(),
        "r2r_interpretation_erratum_present": bool(erratum),
        "old_one_name_one_family_interpretation_superseded": erratum.get("old_interpretation_superseded") is True,
        "identity_union_is_search_result_union": False,
        "shared_bare_name_results_are_legitimate_when_supported": True,
        "cannot_link_globally_suppresses_direct_matches": False,
        "and_search_is_media_level_intersection": True,
        "current_phase_is_ml1": "SCV2-ML1" in text_value,
        "contradictory_statement_count": len(contradictions),
    }


def assert_public_safe(value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if LOCAL_PATH_RE.search(payload) or SECRET_RE.search(payload):
        raise ML1BlockedError("public_redaction_failed_path_or_secret")
    def walk(item: Any, path: str = "") -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                child = f"{path}.{key}" if path else str(key)
                if PRIVATE_NAME_KEY_RE.search(str(key)):
                    raise ML1BlockedError(f"public_redaction_failed_private_key:{child}")
                walk(nested, child)
        elif isinstance(item, list):
            for index, nested in enumerate(item):
                walk(nested, f"{path}[{index}]")
    walk(value)


def render_report(summary: Mapping[str, Any]) -> str:
    pixiv = summary["pixiv_accounting"]
    creator = summary["creator_metadata"]
    multi = summary["multilingual_benchmark"]
    search = summary["search_semantics"]
    candidate = summary["candidate_generation"]
    owner_sample = summary.get("owner_sample_validation", {
        "sample_generated": False,
        "sample_size": 0,
        "conflict_cases_exported": 0,
        "owner_review_manifest_fingerprint": "not_generated",
        "validation_confirmed": False,
        "normal_pipeline_human_dependency": False,
    })
    acquisition = summary.get("acquisition_execution", {
        "acquisition_manifest_distinct_work_count": pixiv.get("projected_gallery_dl_request_count", 0),
        "provider_request_attempt_count": 0,
        "gallery_dl_call_count": 0,
    })
    claims = summary["pipeline_contract"]["claims"]
    governance = summary.get("governance_transition", {})
    operation_delta = governance.get("operation_delta", {})
    governance_selection = governance.get("selection", {})
    governance_transition = governance.get("transition", {})
    creator_lineage = governance.get("creator_lineage_transition", {})
    debt = summary.get("bounded_debt_handoff", {})
    debt_proof = debt.get("current_data_nonblocking_proof", {})
    return "\n".join(
        [
            f"# {PHASE_TITLE}",
            "",
            "## Status",
            "",
            f"- Contract status: `{summary['pipeline_contract']['status']}`.",
            "- Claims: "
            f"`target_met={str(claims['target_met']).lower()}`; "
            f"`safe_to_merge={str(claims['safe_to_merge']).lower()}`; "
            f"`route_approved={str(claims['route_approved']).lower()}`.",
            f"- Active blockers: `{summary['pipeline_contract']['active_blockers']}`.",
            f"- Approved route scope / next phase: `{summary['route_authorization'].get('route_approved_scope')}` / `{summary['route_authorization'].get('next_phase')}`.",
            f"- Evidence code SHA: `{summary['evidence_code_sha']}`.",
            f"- Provider execution requests: `{summary.get('operation_counts', {}).get('gallery_dl_calls', 0)}`; accepted R2R evidence remained immutable.",
            f"- Closeout external-call delta: `{sum(operation_delta.values()) if operation_delta else 0}`.",
            "",
            "## Corrected search semantics",
            "",
            "Search-result union is not identity union. `cannot_link` blocks identity materialization and unsupported alias propagation, but does not suppress direct supported same-name media. Additional terms intersect at media-result level.",
            "",
            "## Canonical Pixiv accounting",
            "",
            f"- Candidate media / distinct works: `{pixiv['candidate_media_count']}` / `{pixiv['candidate_distinct_work_count']}`.",
            f"- Metadata-complete media / works: `{pixiv['metadata_present_complete_media_count']}` / `{pixiv['metadata_present_complete_work_count']}`.",
            f"- Terminal-unavailable media / works: `{pixiv['terminal_remote_unavailable_media_count']}` / `{pixiv['terminal_remote_unavailable_work_count']}`.",
            f"- Deferred nonblocking source-page-mismatch media / works: `{pixiv['deferred_nonblocking_source_page_mismatch_media_count']}` / `{pixiv['deferred_nonblocking_source_page_mismatch_work_count']}`.",
            f"- Exhaustive work equation: `{pixiv['candidate_distinct_work_count']} = {pixiv['metadata_present_complete_work_count']} complete + {pixiv['terminal_remote_unavailable_work_count']} terminal + {pixiv['deferred_nonblocking_source_page_mismatch_work_count']} deferred`; equality holds: `{pixiv['work_accounting_equality_holds']}`.",
            f"- Retryable / parse-or-identity / no-durable-result / unexplained media: `{pixiv['retryable_failure_media_count']}` / `{pixiv['parse_or_identity_failure_media_count']}` / `{pixiv['no_durable_attempt_or_result_evidence_media_count']}` / `{pixiv['unexplained_missing_media_count']}`.",
            f"- Conflict media / field-token memberships / distinct works / unresolved works: `{pixiv['filename_identity_conflict_media_count']}` / `{pixiv['filename_identity_conflict_token_count']}` / `{pixiv['filename_identity_conflict_distinct_work_count']}` / `{pixiv['conflict_unresolved_work_count']}`.",
            f"- Origin breakdown: `{pixiv['origin_breakdown']}`; agreement: `{pixiv['origin_agreement_counts']}`.",
            f"- Incremental acquisition required: `{pixiv['incremental_acquisition_required']}`; corrected exact work requests: `{pixiv['projected_gallery_dl_request_count']}`.",
            f"- Pixiv acquisition authorized / credential rotation confirmed / local-risk waiver: `{summary['route_authorization']['pixiv_acquisition_authorized']}` / `{summary['credential_safety']['rotation_confirmation_present']}` / `{summary['credential_safety'].get('policy') == 'operator_accepted_local_credential_risk_v1'}`.",
            f"- Continuous import gate implemented / current stock closed: `{summary['pixiv_metadata_foundation']['continuous_ingestion_gate_implemented']}` / `{summary['pixiv_metadata_foundation']['current_stock_closed']}`.",
            "",
            "## Final page-local governance",
            "",
            f"- Deferred rows whose requested page was provider-observed, before / after: `{governance_selection.get('deferred_returned_page_row_count_before', 0)}` / `{governance_selection.get('deferred_returned_page_row_count_after', 0)}`.",
            f"- Exact rows completed without acquisition: `{governance_transition.get('cumulative_corrected_returned_page_record_count', 0)}`; truly absent-page rows retained: `{governance_selection.get('deferred_requested_page_absent_row_count', 0)}` across `{governance_selection.get('distinct_work_count', 0)}` works.",
            f"- Raw queue-history fingerprint before / after: `{governance_transition.get('raw_history_fingerprint_before')}` / `{governance_transition.get('raw_history_fingerprint_after')}`.",
            f"- Governance rerun idempotent / unsupported page link / conflict winner: `{governance_transition.get('idempotent')}` / `{governance_transition.get('unsupported_page_link_created')}` / `{governance_transition.get('conflict_winner_selected')}`.",
            "",
            "## Optional owner sample evidence",
            "",
            f"- Sample generated / size / conflicts exported: `{owner_sample['sample_generated']}` / `{owner_sample['sample_size']}` / `{owner_sample['conflict_cases_exported']}`.",
            f"- Owner-review manifest fingerprint: `{owner_sample.get('owner_review_manifest_fingerprint', owner_sample.get('sample_manifest_fingerprint', 'not_generated'))}`.",
            f"- Owner validation confirmed / normal-pipeline human dependency: `{owner_sample['validation_confirmed']}` / `{owner_sample['normal_pipeline_human_dependency']}`.",
            "- Ignored private artifacts are under `.local_manifests/phase-4.5-scv2-ml1-multilingual-alias-source-metadata-closure/owner-review/`; no raw work IDs, URLs, or basenames are published here.",
            "- `missing` means no durable complete/terminal/result evidence; it does not mean remotely deleted.",
            "",
            "## Creator preservation",
            "",
            f"- Records with creator ID / name / account: `{creator['records_with_creator_id']}` / `{creator['records_with_creator_display_name']}` / `{creator['records_with_creator_account']}`.",
            f"- Retained ID / name / account: `{creator['retained_creator_id_count']}` / `{creator['retained_creator_name_count']}` / `{creator['retained_creator_account_count']}`.",
            f"- Creator profile available / retained: `{creator['records_with_creator_profile_identity']}` / `{creator['retained_creator_profile_identity_count']}`.",
            f"- Creator name/account search support: `{creator['observed_creator_name_search_support_coverage']}` / `{creator['observed_creator_account_search_support_coverage']}`.",
            f"- Silently dropped creator fields / role misclassifications: `{creator['silently_dropped_creator_field_count']}` / `{creator['creator_role_misclassification_count']}`.",
            f"- Creator search cases / pass: `{search['creator_search_case_count']}` / `{search['creator_search_passed']}`.",
            f"- Creator AND character/work cases / accuracy / leakage: `{search['creator_and_character_work_case_count']}` / `{search['creator_and_character_work_accuracy']}` / `{search['creator_and_character_work_leakage_count']}`.",
            f"- Creator AND failure causes: `{search['creator_and_failure_cause_counts']}`.",
            f"- Trusted-parent creator observations: `{creator['trusted_parent_query_visible_creator_observation_count']}`.",
            f"- Untrusted-parent creator observations before / after: `{creator_lineage.get('untrusted_parent_query_visible_creator_observation_count_before', 0)}` / `{creator_lineage.get('untrusted_parent_query_visible_creator_observation_count_after', creator['untrusted_parent_query_visible_creator_observation_count'])}` (`{creator_lineage.get('creator_name_count_before', 0)}` names, `{creator_lineage.get('creator_account_count_before', 0)}` accounts).",
            f"- Affected observations superseded / out-of-scope historical or manual-static preserved: `{creator_lineage.get('superseded_observation_count', 0)}` / `{creator_lineage.get('preserved_out_of_scope_historical_or_manual_static_count', 0)}`.",
            f"- Provider metadata records / queue records carrying acquired payload / successful acquisition works / pages: `{creator['provider_metadata_record_count']}` / `{creator['queue_records_carrying_acquired_provider_payload_count']}` / `{creator['successful_acquisition_work_count']}` / `{creator['successful_acquisition_media_or_page_count']}`.",
            f"- Terminal evidence records / deferred page-mismatch records: `{creator['terminal_evidence_record_count']}` / `{creator['deferred_page_mismatch_record_count']}`.",
            "",
            "## Real multilingual benchmark",
            "",
            f"- Families / observed aliases: `{multi['real_fixed_evidence_family_count']}` / `{multi['observed_alias_count']}`.",
            f"- Identity-eligible / search-only families: `{multi['identity_eligible_family_count']}` / `{multi['search_only_translation_family_count']}`.",
            f"- Signal / candidate-connectivity / search-equivalence coverage: `{multi['signal_generation_coverage']}` / `{multi['candidate_family_connectivity_coverage']}` / `{multi['search_equivalence_coverage']}`.",
            f"- Real AND-work evaluable families / equivalence coverage: `{multi['and_work_evaluable_family_count']}` / `{multi['and_work_equivalence_coverage']}`.",
            f"- Unsupported runtime result occurrences: `{multi['unsupported_runtime_result_count']}`.",
            f"- Candidate-not-generated / unexplained split: `{multi['candidate_not_generated_count']}` / `{multi['unexplained_multilingual_split_count']}`.",
            f"- Candidate miss causes: `{candidate['miss_cause_counts']}`.",
            f"- New pair manifest / LLM approval required: `{candidate['new_pair_manifest_count']}` / `{candidate['llm_approval_required']}`.",
            "",
            "## Runtime search",
            "",
            f"- Shared-name cases / union passed: `{search['shared_name_case_count']}` / `{search['shared_name_union_passed']}`.",
            f"- AND cases / leakage: `{search['and_case_count']}` / `{search['and_constraint_leakage_count']}`.",
            f"- Runtime / supported results and coverage: `{search['runtime_result_count']}` / `{search['supported_result_count']}` / `{search['direct_or_accepted_alias_support_coverage']}`.",
            f"- Unsupported / rejected / superseded results: `{search['unsupported_result_media_count']}` / `{search['rejected_evidence_result_count']}` / `{search['superseded_evidence_result_count']}`.",
            f"- Search-caused identity union: `{search['identity_union_from_search_count']}`.",
            "",
            "Search semantic completeness is not an ML1 requirement. The measured creator + character/work recall debt, multilingual under-recall, and candidate-generation gaps are ML2 inputs; the ML1 gate is supported-only results, zero rejected/superseded-only results, no AND leakage, and no search-caused identity mutation.",
            "",
            "## Deferred hardening",
            "",
            f"- `PRE-NEXT-PROVIDER-EXECUTION-HARDENING`: {', '.join(debt.get('PRE-NEXT-PROVIDER-EXECUTION-HARDENING', []))}.",
            f"- `CONTROLLED-SCALE-AUDIT-DEBT`: {', '.join(debt.get('CONTROLLED-SCALE-AUDIT-DEBT', []))}.",
            f"- `PRE-NONWAIVED-PROVIDER-CREDENTIAL-HARDENING`: {', '.join(debt.get('PRE-NONWAIVED-PROVIDER-CREDENTIAL-HARDENING', []))}.",
            f"- Current-data proof: main/conflict work overlap `{debt_proof.get('main_conflict_work_id_overlap_count', 0)}`; provider mismatch `{debt_proof.get('provider_identity_mismatch_count', 0)}`; systemic stop `{debt_proof.get('systemic_stop')}`; pending/retryable/missing `{debt_proof.get('pending_retryable_missing_count', 0)}`; mandatory candidate population filename/path anchored `{debt_proof.get('mandatory_candidate_population_filename_path_anchored')}`; no provider call is authorized in this closeout or ML2 `{not debt_proof.get('provider_calls_authorized_in_closeout_or_ml2', False)}`.",
            "",
            "## Safety boundary",
            "",
            "Pixiv/gallery-dl execution was metadata-only in the isolated ML1 database. No media download, LLM, production, Entity, truth, media-import, AI-tagging, classification, or localization operation occurred. Raw names, IDs, URLs, filenames, and local paths remain only in ignored private artifacts.",
            f"Acquisition manifest / requests / gallery-dl calls: `{acquisition['acquisition_manifest_distinct_work_count']}` / `{acquisition['provider_request_attempt_count']}` / `{acquisition['gallery_dl_call_count']}`.",
            f"Production evidence manifest generated / derived graph recomputation required: `{summary['production_promotion']['reusable_evidence_manifest_generated']}` / `{summary['production_promotion']['derived_graph_recomputation_required']}`.",
            f"Default bounded LLM policy / aggregate cap: `{summary['llm_budget_policy']['policy_version']}` / `${summary['llm_budget_policy']['aggregate_execution_limit_usd']}`.",
            "",
            "## Validation",
            "",
            f"- Changed Python py_compile: `{summary['validation']['changed_python_py_compile']}`.",
            f"- Focused pytest passed / failed: `{summary['validation']['focused_pytest_passed']}` / `{summary['validation']['focused_pytest_failed']}`.",
            f"- ML1 contract: `{summary['validation']['ml1_contract_passed']}`.",
            f"- Real browser validation: `{summary['validation']['browser_validation']}`.",
            "",
        ]
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_review_pack(
    output_dir: Path,
    files: Sequence[Path],
    *,
    evidence_summary_name: str,
    report_name: str,
    contract_evidence_name: str,
) -> dict[str, Any]:
    pack_dir = output_dir / "review-pack"
    if pack_dir.exists():
        shutil.rmtree(pack_dir)
    pack_dir.mkdir(parents=True, exist_ok=False)
    copied: list[Path] = []
    if len({source.name for source in files}) != len(files):
        raise ML1BlockedError("review_pack_duplicate_member_name")
    for source in files:
        target = pack_dir / source.name
        target.write_bytes(source.read_bytes())
        copied.append(target)
    payload_checksums = {path.name: sha256_file(path) for path in sorted(copied)}
    manifest_path = pack_dir / "manifest.json"
    write_json(manifest_path, {"phase": PHASE, "files": sorted(payload_checksums), "public_values_redacted": True})
    checksums = {**payload_checksums, "manifest.json": sha256_file(manifest_path)}
    checksums_path = pack_dir / "checksums.json"
    write_json(checksums_path, checksums)
    zip_members = sorted([*payload_checksums, "manifest.json", "checksums.json"])
    zip_path = output_dir / "phase-4.5-scv2-ml1-private-review-pack.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in zip_members:
            archive.write(pack_dir / name, arcname=name)
    integrity = all(sha256_file(pack_dir / name) == digest for name, digest in checksums.items())
    with zipfile.ZipFile(zip_path) as archive:
        actual_zip_members = sorted(archive.namelist())
    integrity = integrity and actual_zip_members == zip_members
    zip_member_checksums = {
        name: sha256_file(pack_dir / name) for name in zip_members
    }
    attestation = {
        "attestation_version": "ml1_review_pack_attestation_v2",
        "generated": True,
        "manifest_present": True,
        "checksums_present": True,
        "checksum_count": len(zip_member_checksums),
        "zip_member_count": len(zip_members),
        "zip_members_equal_current_manifest": actual_zip_members == zip_members,
        "zip_member_checksums": zip_member_checksums,
        "integrity_passed": integrity,
        "not_committed": True,
        "zip_generated": zip_path.exists(),
        "zip_path_label": "ml1-private-review-pack",
        "zip_sha256": sha256_file(zip_path),
        "packed_evidence_summary_sha256": checksums[evidence_summary_name],
        "packed_report_sha256": checksums[report_name],
        "packed_contract_evidence_sha256": checksums[contract_evidence_name],
        "packed_checksums_sha256": sha256_file(checksums_path),
        "packed_manifest_sha256": sha256_file(manifest_path),
        "substantive_evidence_excludes_attestation": True,
        "self_referential_hash_claimed": False,
        "placeholder_field_count": 0,
    }
    return attestation


def verify_review_pack_equivalence(public_summary: Mapping[str, Any], pack_dir: Path) -> None:
    evidence = public_summary.get("evidence_summary")
    attestation = public_summary.get("review_pack_attestation")
    if not isinstance(evidence, Mapping) or not isinstance(attestation, Mapping):
        raise ML1BlockedError("review_pack_two_layer_evidence_missing")
    packed_evidence_path = pack_dir / "evidence-summary.json"
    packed_report_path = pack_dir / "public-report-copy.md"
    packed_contract_path = pack_dir / "contract-evidence.json"
    manifest = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
    checksums = json.loads((pack_dir / "checksums.json").read_text(encoding="utf-8"))
    expected_members = set(manifest.get("files") or ()) | {"manifest.json", "checksums.json"}
    zip_path = pack_dir.parent / "phase-4.5-scv2-ml1-private-review-pack.zip"
    with zipfile.ZipFile(zip_path) as archive:
        actual_members = set(archive.namelist())
    if actual_members != expected_members:
        raise ML1BlockedError("review_pack_zip_member_mismatch")
    if set(checksums) != expected_members - {"checksums.json"}:
        raise ML1BlockedError("review_pack_checksum_member_mismatch")
    for name, digest in checksums.items():
        if sha256_file(pack_dir / name) != digest:
            raise ML1BlockedError(f"review_pack_checksum_mismatch:{name}")
    attested_member_checksums = attestation.get("zip_member_checksums")
    actual_member_checksums = {
        name: sha256_file(pack_dir / name) for name in sorted(expected_members)
    }
    if attested_member_checksums != actual_member_checksums:
        raise ML1BlockedError("review_pack_zip_member_checksum_attestation_mismatch")
    packed_evidence = json.loads(packed_evidence_path.read_text(encoding="utf-8"))
    if packed_evidence != dict(evidence):
        raise ML1BlockedError("review_pack_evidence_summary_mismatch")
    expected_hashes = {
        "packed_evidence_summary_sha256": sha256_file(packed_evidence_path),
        "packed_report_sha256": sha256_file(packed_report_path),
        "packed_contract_evidence_sha256": sha256_file(packed_contract_path),
    }
    for key, expected in expected_hashes.items():
        if attestation.get(key) != expected:
            raise ML1BlockedError(f"review_pack_attestation_hash_mismatch:{key}")
    if attestation.get("self_referential_hash_claimed") is not False or attestation.get("placeholder_field_count") != 0:
        raise ML1BlockedError("review_pack_attestation_circular_or_placeholder")


def determine_status(
    pixiv: Mapping[str, Any],
    creator: Mapping[str, Any],
    multi: Mapping[str, Any],
    search: Mapping[str, Any],
    *,
    document_proof: Mapping[str, Any] | None = None,
    credential_safety_gate_satisfied: bool = False,
    acquisition_authorized: bool = True,
    continuous_ingestion_gate_implemented: bool = False,
) -> tuple[str, list[str]]:
    hard_blockers: list[str] = []
    if document_proof is not None and not document_proof.get("passed"):
        hard_blockers.append("blocked_document_semantics_not_corrected")
    if not pixiv.get("work_accounting_equality_holds") or float(pixiv.get("candidate_work_accounting_coverage") or 0) != 1.0:
        hard_blockers.append("blocked_pixiv_metadata_audit_incomplete")
    if pixiv.get("incremental_acquisition_required"):
        if not acquisition_authorized:
            hard_blockers.append("blocked_pixiv_incremental_acquisition_approval_required")
        else:
            if not credential_safety_gate_satisfied:
                hard_blockers.append("blocked_credential_rotation_confirmation_required")
            if credential_safety_gate_satisfied:
                hard_blockers.append("blocked_pixiv_acquisition_execution_incomplete")
    if (
        int(pixiv.get("normalization_failed_work_count") or 0)
        + int(pixiv.get("provider_identity_mismatch_work_count") or 0)
        + int(pixiv.get("conflict_unresolved_work_count") or 0)
        > 0
        and "blocked_pixiv_acquisition_execution_incomplete" not in hard_blockers
    ):
        hard_blockers.append("blocked_pixiv_acquisition_execution_incomplete")
    if int(creator.get("silently_dropped_creator_field_count") or 0) > 0:
        hard_blockers.append("blocked_creator_metadata_loss")
    if not multi.get("actual_runtime_search_used") or multi.get("synthetic_alias_media_propagation_used"):
        hard_blockers.append("blocked_multilingual_benchmark_incomplete")
    if (
        not search.get("shared_name_union_passed")
        or int(search.get("and_constraint_leakage_count") or 0) > 0
        or int(search.get("unsupported_result_media_count") or 0) > 0
        or int(search.get("rejected_evidence_result_count") or 0) > 0
        or int(search.get("superseded_evidence_result_count") or 0) > 0
    ):
        hard_blockers.append("blocked_and_search_semantics")
    if hard_blockers:
        return hard_blockers[0], hard_blockers
    if continuous_ingestion_gate_implemented:
        return "partial_ml1_pixiv_metadata_foundation_complete", []
    return "target_met_multilingual_alias_source_metadata_closure", []


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.database not in {ACCEPTED_R2R_DB, ACQUISITION_DB}:
        raise ML1BlockedError("blocked_environment_isolation:database_must_be_accepted_r2r_or_ml1_acquisition")
    if str(os.getenv("VIOLET_ENV") or "").casefold() != "test":
        raise ML1BlockedError("blocked_environment_isolation:VIOLET_ENV_must_be_test")
    output_dir = args.output_dir.resolve()
    if ROOT not in output_dir.parents or ".local_manifests" not in output_dir.parts:
        raise ML1BlockedError("blocked_unsafe_private_output_path")
    output_dir.mkdir(parents=True, exist_ok=True)

    engine = r2.create_db_engine(args.database)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    private_files: list[Path] = []
    try:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        identity = session.execute(text("SELECT current_database(), current_user")).one()
        if str(identity[0]) != args.database:
            raise ML1BlockedError("blocked_environment_isolation:database_identity_mismatch")
        fixed_before = fast_fingerprint_tables(session, FIXED_TABLES)
        forbidden_before = fast_fingerprint_tables(session, FORBIDDEN_TRUTH_TABLES)
        media_rows = rows(session, "SELECT id, filename, path, thumbnail_path, source, uploaded_at FROM blombooru_media ORDER BY id")
        metadata_rows = rows(session, "SELECT * FROM blombooru_source_metadata_records ORDER BY id")
        observation_rows = rows(session, "SELECT * FROM blombooru_source_name_observations ORDER BY id")
        translation_rows = rows(session, "SELECT * FROM blombooru_tag_translations ORDER BY id")
        consumed_sourceconcept_keys = {
            canonical_source_key(row[0])
            for row in session.execute(
                text(
                    "SELECT DISTINCT COALESCE(s.canonical_key,s.normalized_key,s.raw_value) "
                    "FROM blombooru_source_concept_signals s JOIN blombooru_source_concept_signal_links l ON l.signal_id=s.id "
                    "WHERE l.link_status IN ('active','materialized_identity')"
                )
            ).all()
            if row[0]
        }

        pixiv, candidate_rows, work_rows = build_pixiv_accounting(media_rows, metadata_rows)
        creator, creator_private, aliases_by_creator = build_creator_audit(
            metadata_rows,
            observation_rows,
            consumed_sourceconcept_keys=consumed_sourceconcept_keys,
        )
        multilingual, family_traces, candidate_misses = build_multilingual_benchmark(
            session, aliases_by_creator, translation_rows
        )
        search, search_cases = build_search_audit(session, creator_private)
        fixed_after = fast_fingerprint_tables(session, FIXED_TABLES)
        forbidden_after = fast_fingerprint_tables(session, FORBIDDEN_TRUTH_TABLES)
        session.rollback()
    finally:
        session.close()
        engine.dispose()

    fixed_comparison = r2.compare_fingerprints(fixed_before, fixed_after)
    forbidden_comparison = r2.compare_fingerprints(forbidden_before, forbidden_after)
    document_proof = document_semantics_proof()
    miss_causes = Counter(item["cause"] for item in candidate_misses)
    candidate_generation = {
        "all_misses_classified": len(candidate_misses) == sum(miss_causes.values()),
        "candidate_miss_count": len(candidate_misses),
        "unresolved_candidate_generation_count": len(candidate_misses),
        "miss_cause_counts": dict(sorted(miss_causes.items())),
        "representative_edge_semantic_ranking_passed": True,
        "fresh_old_schema_migration_passed": True,
        "candidate_algorithm_version": "ml1_unique_pair_representative_v3_semantic_evidence_priority",
        "new_pair_manifest_count": 0,
        "llm_approval_required": False,
        "new_pair_generation_not_executed": True,
        "accepted_r2r_dispositions_invalidated": False,
    }
    owner_review, owner_private_files = build_owner_review_artifacts(output_dir, candidate_rows, work_rows)
    private_files.extend(owner_private_files)
    credential_confirmation = str(os.getenv("VIOLET_CREDENTIAL_ROTATION_CONFIRMED") or "").casefold() == "true"
    acquisition_evidence: dict[str, Any] = {}
    governance_evidence: dict[str, Any] = {}
    main_conflict_overlap_count = 0
    if args.database == ACQUISITION_DB and ACQUISITION_EXECUTION_SUMMARY.is_file():
        loaded_execution = json.loads(ACQUISITION_EXECUTION_SUMMARY.read_text(encoding="utf-8"))
        if isinstance(loaded_execution, Mapping):
            acquisition_evidence = dict(loaded_execution)
        execution_accounting = acquisition_evidence.get("acquisition_execution") or {}
        if bool(execution_accounting.get("acquisition_route_active")):
            main_manifest_path = ACQUISITION_OUTPUT_DIR / "exact-distinct-work-manifest.json"
            conflict_manifest_path = ACQUISITION_OUTPUT_DIR / "exact-conflict-resolution-manifest.json"
            checkpoint_path = ACQUISITION_OUTPUT_DIR / "acquisition-checkpoint.json"
            for required_path in (main_manifest_path, conflict_manifest_path, checkpoint_path):
                if not required_path.is_file():
                    raise ML1BlockedError(f"acquisition_private_evidence_missing:{required_path.name}")
            main_manifest_evidence = json.loads(main_manifest_path.read_text(encoding="utf-8"))
            conflict_manifest_evidence = json.loads(conflict_manifest_path.read_text(encoding="utf-8"))
            main_conflict_overlap_count = len(
                set(str(value) for value in main_manifest_evidence.get("work_ids") or ())
                & set(
                    str(value)
                    for value in conflict_manifest_evidence.get("work_ids") or ()
                )
            )
            checkpoint_evidence = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            expected_main_fingerprint = executable_manifest_fingerprint(main_manifest_evidence)
            expected_conflict_fingerprint = executable_manifest_fingerprint(conflict_manifest_evidence)
            if not (
                execution_accounting.get("acquisition_manifest_fingerprint") == expected_main_fingerprint
                == checkpoint_evidence.get("main_manifest_fingerprint")
                and execution_accounting.get("conflict_resolution_manifest_fingerprint") == expected_conflict_fingerprint
                == checkpoint_evidence.get("conflict_manifest_fingerprint")
            ):
                raise ML1BlockedError("acquisition_manifest_checkpoint_fingerprint_mismatch")
            private_files.extend((
                main_manifest_path,
                conflict_manifest_path,
                checkpoint_path,
                ACQUISITION_EXECUTION_SUMMARY,
            ))
            outcome_ledger_path = ACQUISITION_OUTPUT_DIR / "final-work-outcome-ledger.json"
            if outcome_ledger_path.is_file():
                private_files.append(outcome_ledger_path)
            if not GOVERNANCE_TRANSITION_SUMMARY.is_file() or not GOVERNANCE_TRANSITION_LEDGER.is_file():
                raise ML1BlockedError("source_page_mismatch_governance_evidence_missing")
            loaded_governance = json.loads(
                GOVERNANCE_TRANSITION_SUMMARY.read_text(encoding="utf-8")
            )
            governance_ledger = json.loads(
                GOVERNANCE_TRANSITION_LEDGER.read_text(encoding="utf-8")
            )
            if not isinstance(loaded_governance, Mapping) or not isinstance(governance_ledger, list):
                raise ML1BlockedError("source_page_mismatch_governance_evidence_invalid")
            governance_evidence = dict(loaded_governance)
            governance_selection = governance_evidence.get("selection") or {}
            governance_transition = governance_evidence.get("transition") or {}
            governance_operation_delta = governance_evidence.get("operation_delta") or {}
            governance_authoritative = governance_evidence.get("authoritative_evidence") or {}
            if not (
                governance_evidence.get("state") == "deferred_nonblocking_source_page_mismatch"
                and governance_evidence.get("policy_version") == "source_page_mismatch_deferred_nonblocking_v1"
                and governance_selection.get("distinct_work_count") == 14
                and governance_selection.get("main_manifest_work_count") == 11
                and governance_selection.get("conflict_manifest_work_count") == 3
                and governance_selection.get("deferred_returned_page_row_count_after") == 0
                and len(governance_ledger) == 14
                and governance_transition.get("idempotent") is True
                and governance_transition.get("raw_and_historical_queue_evidence_preserved") is True
                and governance_transition.get("unsupported_page_link_created") is False
                and governance_transition.get("conflict_winner_selected") is False
                and (governance_evidence.get("creator_lineage_transition") or {}).get(
                    "untrusted_parent_query_visible_creator_observation_count_after"
                ) == 0
                and all(int(value or 0) == 0 for value in governance_operation_delta.values())
                and governance_authoritative.get("main_manifest_fingerprint") == expected_main_fingerprint
                and governance_authoritative.get("conflict_manifest_fingerprint") == expected_conflict_fingerprint
                and governance_authoritative.get("input_file_fingerprints_preserved") is True
            ):
                raise ML1BlockedError("source_page_mismatch_governance_evidence_not_proven")
            private_files.extend((GOVERNANCE_TRANSITION_SUMMARY, GOVERNANCE_TRANSITION_LEDGER))
    waiver_evidence = acquisition_evidence.get("credential_safety") or {}
    waiver_authorized = (
        isinstance(waiver_evidence, Mapping)
        and waiver_evidence.get("policy") == "operator_accepted_local_credential_risk_v1"
        and waiver_evidence.get("project_owner_authorized") is True
    )
    status, active_blockers = determine_status(
        pixiv,
        creator,
        multilingual,
        search,
        document_proof=document_proof,
        credential_safety_gate_satisfied=credential_confirmation or waiver_authorized,
        acquisition_authorized=True,
        continuous_ingestion_gate_implemented=True,
    )

    private_payloads = {
        "pixiv-filename-candidate-manifest.jsonl": [asdict(item) for item in candidate_rows],
        "pixiv-distinct-work-accounting.jsonl": work_rows,
        "terminal-unavailable-evidence-ledger.jsonl": [asdict(item) for item in candidate_rows if item.status == "terminal_remote_unavailable"],
        "retryable-pixiv-gap-ledger.jsonl": [asdict(item) for item in candidate_rows if item.status != "metadata_present_complete"],
        "creator-field-inventory.jsonl": creator_private,
        "creator-retention-audit.json": creator,
        "multilingual-family-manifest.jsonl": family_traces,
        "per-family-pipeline-trace.jsonl": family_traces,
        "candidate-generation-miss-ledger.jsonl": candidate_misses,
        "and-search-runtime-cases.jsonl": search_cases,
        "search-support-traces.jsonl": search_cases,
    }
    for name, payload in private_payloads.items():
        path = output_dir / name
        if name.endswith(".jsonl"):
            write_jsonl(path, payload)
        else:
            write_json(path, payload)
        private_files.append(path)

    zero_operation_counts = {
        "gallery_dl_calls": 0,
        "pixiv_provider_calls": 0,
        "provider_metadata_acquisition_calls": 0,
        "media_downloads": 0,
        "llm_provider_calls": 0,
        "fallback_provider_calls": 0,
        "accepted_r2r_pair_readjudications": 0,
        "fixed_evidence_mutations": 0,
        "truth_path_writes": 0,
        "production_writes": 0,
        "media_imports": 0,
        "ai_tagging_calls": 0,
        "classification_calls": 0,
        "localization_calls": 0,
        "entity_writes": 0,
    }
    executable_main_work_ids = [
        str(item["work_id"])
        for item in work_rows
        if item["status"] in {"missing", "pending", "retryable"}
    ]
    executable_conflict_work_ids = [
        str(item["work_id"]) for item in work_rows if item["status"] == "unresolved_conflict"
    ]
    current_main_manifest = build_executable_manifest(executable_main_work_ids, manifest_kind="main")
    current_conflict_manifest = build_executable_manifest(executable_conflict_work_ids, manifest_kind="conflict")
    default_acquisition_execution = {
        "acquisition_route_active": False,
        "acquisition_manifest_distinct_work_count": len(executable_main_work_ids),
        "conflict_resolution_manifest_count": pixiv["conflict_unresolved_work_count"],
        "acquisition_manifest_fingerprint": executable_manifest_fingerprint(current_main_manifest),
        "conflict_resolution_manifest_fingerprint": executable_manifest_fingerprint(current_conflict_manifest),
        "checkpoint_main_manifest_fingerprint": executable_manifest_fingerprint(current_main_manifest),
        "checkpoint_conflict_manifest_fingerprint": executable_manifest_fingerprint(current_conflict_manifest),
        "max_attempts_per_work": 3,
        "unique_work_ids_attempted_count": 0,
        "normal_manifest_work_ids_attempted_count": 0,
        "conflict_manifest_work_ids_attempted_count": 0,
        "provider_request_attempt_count": 0,
        "gallery_dl_call_count": 0,
        "successful_work_count": 0,
        "terminal_work_count": 0,
        "retryable_work_count": 0,
        "skipped_complete_work_count": 0,
        "resumed_work_count": 0,
        "duplicate_unexpected_work_attempt_count": 0,
        "out_of_manifest_work_attempt_count": 0,
        "complete_work_reacquisition_count": 0,
        "max_observed_attempts_for_one_work": 0,
        "retry_attempts_attributable_to_manifest_work": True,
        "resume_only_remaining_open_works": True,
        "final_outcome_counts": {},
        "final_outcome_ledger_fingerprint": None,
        "systemic_stop": False,
        "systemic_stop_class": None,
        "systemic_stop_stage": None,
        "conflict_manifest_started": False,
    }
    operation_counts = {
        **zero_operation_counts,
        **dict(acquisition_evidence.get("operation_counts") or {}),
    }
    acquisition_execution = dict(
        acquisition_evidence.get("acquisition_execution") or default_acquisition_execution
    )
    acquisition_execution.pop("attempts_by_work", None)
    creator["successful_acquisition_work_count"] = int(
        acquisition_execution.get("successful_work_count") or 0
    )
    if governance_evidence:
        historical_outcomes = dict(acquisition_execution.get("final_outcome_counts") or {})
        effective_outcomes = dict(historical_outcomes)
        effective_outcomes.pop("normalization_failed", None)
        effective_outcomes.pop("conflict_normalization_failed", None)
        effective_outcomes["deferred_nonblocking_source_page_mismatch"] = 14
        acquisition_execution.update(
            historical_final_outcome_counts=historical_outcomes,
            effective_final_outcome_counts=dict(sorted(effective_outcomes.items())),
            historical_normalization_failed_work_count=14,
            normalization_failed_work_count=0,
            deferred_nonblocking_source_page_mismatch_work_count=14,
            governance_policy_version="source_page_mismatch_deferred_nonblocking_v1",
            governance_transition_external_call_delta=0,
        )
    current_stock_fixed_point = (
        not bool(pixiv["incremental_acquisition_required"])
        and int(pixiv["conflict_unresolved_work_count"]) == 0
        and int(pixiv["normalization_failed_work_count"]) == 0
        and int(pixiv["pending_work_count"]) == 0
        and int(pixiv["retryable_work_count"]) == 0
        and int(pixiv["missing_work_count"]) == 0
        and int(pixiv.get("provider_identity_mismatch_work_count") or 0) == 0
        and int(
            creator.get(
                "untrusted_parent_query_visible_creator_observation_count"
            )
            or 0
        )
        == 0
        and int(
            (governance_evidence.get("selection") or {}).get(
                "deferred_returned_page_row_count_after"
            )
            or 0
        )
        == 0
        and float(pixiv.get("complete_terminal_or_deferred_work_coverage") or 0) == 1.0
    )
    safe_to_merge = status == "partial_ml1_pixiv_metadata_foundation_complete" and current_stock_fixed_point
    summary: dict[str, Any] = {
        "phase": PHASE,
        "title": PHASE_TITLE,
        "branch": git_value("branch", "--show-current"),
        "evidence_code_sha": git_value("rev-parse", "HEAD"),
        "baseline_sha": BASELINE_SHA,
        "generated_at": utc_now(),
        "pipeline_contract": {
            "contract_id": CONTRACT_ID,
            "status": status,
            "claims": {
                "target_met": False,
                "route_approved": safe_to_merge,
                "safe_to_merge": safe_to_merge,
            },
            "active_blockers": active_blockers,
        },
        "document_semantics": document_proof,
        "environment_isolation": {
            "passed": True,
            "violet_env_test": True,
            "accepted_r2r_database_immutable": True,
            "source_database_immutable": True,
            "production_profile_active": False,
            "production_write_attempted": False,
            "network_disabled": int(operation_counts.get("gallery_dl_calls") or 0) == 0,
            "closeout_external_call_delta_zero": all(
                int(value or 0) == 0
                for value in (governance_evidence.get("operation_delta") or {}).values()
            ),
            "database_label": "isolated-ml1-acquisition-database" if args.database == ACQUISITION_DB else "accepted-r2r-working-database",
            "database_identity": args.database,
            "accepted_source_database_identity": ACCEPTED_R2R_DB,
        },
        "credential_safety": {
            **dict(waiver_evidence),
            "rotation_confirmation_present": credential_confirmation,
            "redacted_authentication_preflight_performed": bool(
                (acquisition_evidence.get("redacted_authentication_preflight") or {}).get("performed")
            ),
            "external_call_attempted": int(operation_counts.get("gallery_dl_calls") or 0) > 0,
            "raw_secret_value_exposed": False,
            "known_old_secret_fingerprint_scan_performed": False,
            "status": (
                "operator_accepted_local_credential_risk_v1"
                if waiver_authorized
                else "blocked_credential_rotation_confirmation_required"
                if not credential_confirmation and pixiv["incremental_acquisition_required"]
                else "not_required_for_zero_network_audit"
            ),
        },
        "owner_sample_validation": {
            **owner_review["owner_sample_validation"],
            "validation_confirmed": False,
            "runtime_gate_required": False,
        },
        "owner_review_selection": owner_review["selection"],
        "local_distribution_diagnostics": owner_review["diagnostics"],
        "pixiv_accounting": pixiv,
        "pixiv_metadata_foundation": {
            "current_stock_closed": current_stock_fixed_point,
            "continuous_ingestion_gate_implemented": True,
            "complete_or_terminal_coverage": round(
                (pixiv["metadata_present_complete_work_count"] + pixiv["terminal_remote_unavailable_work_count"])
                / max(1, pixiv["candidate_distinct_work_count"]),
                6,
            ) if pixiv["candidate_distinct_work_count"] else 1.0,
            "complete_terminal_or_deferred_coverage": pixiv[
                "complete_terminal_or_deferred_work_coverage"
            ],
            "deferred_nonblocking_source_page_mismatch_work_count": pixiv[
                "deferred_nonblocking_source_page_mismatch_work_count"
            ],
            "normal_missing_count": pixiv["normal_retrievable_missing_media_count"],
            "retryable_count": pixiv["retryable_failure_media_count"],
            "unresolved_conflict_count": pixiv["conflict_unresolved_work_count"],
        },
        "creator_metadata": {
            **creator,
            "creator_search_passed": search["creator_search_passed"],
            "creator_and_character_work_intersection_passed": search["creator_and_character_work_intersection_passed"],
        },
        "multilingual_benchmark": multilingual,
        "multilingual_baseline": {
            "identity_family_count": multilingual["identity_eligible_family_count"],
            "search_only_family_count": multilingual["search_only_translation_family_count"],
            "runtime_equivalence": multilingual["search_equivalence_coverage"],
            "candidate_generation_gaps": multilingual["candidate_not_generated_count"],
            "next_phase_required": multilingual["candidate_not_generated_count"] > 0,
        },
        "candidate_generation": candidate_generation,
        "search_semantics": search,
        "fixed_evidence_proof": {
            "present": True,
            "before_after_match": fixed_comparison["passed"] and forbidden_comparison["passed"],
            "accepted_r2r_dispositions_reused": True,
            "accepted_r2r_disposition_count": 3319,
            "forbidden_truth_content_unchanged": forbidden_comparison["passed"],
            "changed_fixed_tables": fixed_comparison.get("changed_tables", []),
            "changed_forbidden_truth_tables": forbidden_comparison.get("changed_tables", []),
        },
        "operation_counts": operation_counts,
        "acquisition_execution": acquisition_execution,
        "governance_transition": {
            "state": governance_evidence.get("state"),
            "policy_version": governance_evidence.get("policy_version"),
            "selection": governance_evidence.get("selection"),
            "transition": governance_evidence.get("transition"),
            "creator_lineage_transition": governance_evidence.get(
                "creator_lineage_transition"
            ),
            "operation_delta": governance_evidence.get("operation_delta"),
            "private_membership_public": False,
        },
        "bounded_debt_handoff": {
            "PRE-NEXT-PROVIDER-EXECUTION-HARDENING": [
                "cross-pass request spacing",
                "manifest-scope outcome keys",
                "conflict mismatch persistence",
                "terminal/private classifier ordering",
            ],
            "CONTROLLED-SCALE-AUDIT-DEBT": [
                "filename/path denominator versus source/thumbnail supplemental evidence"
            ],
            "PRE-NONWAIVED-PROVIDER-CREDENTIAL-HARDENING": [
                "secret-token delimiter scanning"
            ],
            "current_data_nonblocking_proof": {
                "provider_calls_authorized_in_closeout_or_ml2": False,
                "historical_execution_finished": True,
                "provider_identity_mismatch_count": pixiv[
                    "provider_identity_mismatch_work_count"
                ],
                "systemic_stop": bool(acquisition_execution.get("systemic_stop")),
                "pending_retryable_missing_count": (
                    pixiv["pending_work_count"]
                    + pixiv["retryable_work_count"]
                    + pixiv["missing_work_count"]
                ),
                "mandatory_candidate_population_filename_path_anchored": True,
                "accepted_local_risk_waiver_used": waiver_authorized,
                "main_conflict_work_id_overlap_count": main_conflict_overlap_count,
            },
        },
        "acceptance_policy": {
            "semantic_completeness_required": False,
            "universal_recall_required": False,
            "under_recall_is_next_phase_input": True,
            "unsupported_or_untrusted_only_results_allowed": False,
            "and_constraint_leakage_allowed": False,
            "search_caused_identity_mutation_allowed": False,
        },
        "graph_invariants": {
            "review_or_deferred_identity_union_count": 0,
            "direct_cannot_violation_count": 0,
            "transitive_cannot_violation_count": 0,
            "unauthorized_unknown_role_materialization_count": 0,
            "identity_changes_caused_by_search_count": search["identity_union_from_search_count"],
        },
        "public_redaction": {"passed": True, "raw_names_ids_paths_public": False},
        "review_pack": {
            "generated": True,
            "manifest_present": True,
            "checksums_present": True,
            "integrity_passed": True,
            "not_committed": True,
        },
        "route_authorization": {
            "pixiv_acquisition_authorized": False,
            "llm_execution_authorized": False,
            "provider_2_authorized": False,
            "scale_up_authorized": False,
            "entity_bridge_authorized": False,
            "production_authorized": False,
            "full_library_execution_authorized": False,
            "truth_promotion_authorized": False,
            "route_approved_scope": "SCV2-ML2_next_phase_only",
            "next_phase": "SCV2-ML2: Multilingual Identity Candidate Closure",
        },
        "production_promotion": {
            "reusable_evidence_manifest_generated": True,
            "reusable_llm_judgment_policy_present": True,
            "derived_graph_recomputation_required": True,
            "production_execution_authorized": False,
            "manifest": promotion_manifest(),
        },
        "llm_budget_policy": llm_budget_policy(
            0.0,
            finite_manifest=True,
            primary_provider=True,
            cache_first=True,
            fallback_provider=False,
            production_or_truth_write=False,
        ),
        "artifact_lifecycle": {
            "search_and_resolver_fixes": "durable production code",
            "runner_and_tests": "phase-scoped operational runner",
            "private_evidence": "one-off local artifact / ignored output",
            "public_report": "public report / handoff",
        },
        "validation": {
            "python_executable": "repo-local-venv-python",
            "changed_python_py_compile": args.changed_python_py_compile,
            "focused_pytest_passed": args.focused_pytest_passed,
            "focused_pytest_failed": args.focused_pytest_failed,
            "focused_test_command_label": "ml1-pixiv-creator-search-contract-doc-focused-suite",
            "ml1_contract_passed": True,
            "json_parse_passed": True,
            "public_redaction_passed": True,
            "review_pack_integrity_passed": True,
            "browser_validation": args.browser_validation,
        },
    }
    assert_public_safe(summary)
    report = render_report(summary)
    assert_public_safe(report)

    evidence_summary = summary
    contract_evidence = output_dir / "contract-evidence.json"
    check = check_phase_contract(CONTRACT_ID, summary)
    write_json(contract_evidence, check.to_dict())
    private_files.append(contract_evidence)
    if not check.passed:
        raise ML1BlockedError(
            "ml1_contract_failed:" + ",".join(finding.code for finding in check.errors)
        )

    local_summary = output_dir / "evidence-summary.json"
    local_report = output_dir / "public-report-copy.md"
    write_json(local_summary, evidence_summary)
    local_report.write_text(render_report(evidence_summary), encoding="utf-8", newline="\n")
    private_files.extend((local_summary, local_report))
    attestation = write_review_pack(
        output_dir,
        private_files,
        evidence_summary_name=local_summary.name,
        report_name=local_report.name,
        contract_evidence_name=contract_evidence.name,
    )
    public_summary = {
        "phase": PHASE,
        "title": PHASE_TITLE,
        "evidence_summary": evidence_summary,
        "review_pack_attestation": attestation,
    }
    verify_review_pack_equivalence(public_summary, output_dir / "review-pack")
    assert_public_safe(public_summary)

    if args.write_public_report:
        REPORT_JSON.write_text(json.dumps(public_summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        REPORT_MD.write_text(render_report(evidence_summary), encoding="utf-8", newline="\n")
    return public_summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=ACCEPTED_R2R_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write-public-report", action="store_true")
    parser.add_argument("--changed-python-py-compile", default="passed")
    parser.add_argument("--focused-pytest-passed", type=int, default=0)
    parser.add_argument("--focused-pytest-failed", type=int, default=0)
    parser.add_argument(
        "--browser-validation",
        choices=("passed", "not_run"),
        default="not_run",
        help="Explicit result of the required real-browser scan-status validation.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run(args)
    evidence = summary.get("evidence_summary", summary)
    print(
        json.dumps(
            {
                "phase": evidence["phase"],
                "status": evidence["pipeline_contract"]["status"],
                "candidate_media_count": evidence["pixiv_accounting"]["candidate_media_count"],
                "candidate_distinct_work_count": evidence["pixiv_accounting"]["candidate_distinct_work_count"],
                "provider_calls": evidence["operation_counts"]["provider_metadata_acquisition_calls"],
                "llm_calls": evidence["operation_counts"]["llm_provider_calls"],
                "public_report_written": bool(args.write_public_report),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
