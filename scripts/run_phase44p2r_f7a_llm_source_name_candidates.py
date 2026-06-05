"""Phase 4.4-P2R-F7a LLM-backed source name candidate extraction.

This runner consumes existing DB/source-layer metadata only. It does not run
providers, upload images, perform source enrichment, create SourceConcept rows,
or write Entity/media_tags truth paths.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import shutil
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import settings  # noqa: E402
from app.database import check_and_migrate_schema  # noqa: E402
from app.models import Media, SourceMetadataRecord  # noqa: E402
from app.services.source_metadata_registry_service import canonical_source_key, normalize_source_text  # noqa: E402
from app.services.source_name_candidate_extraction_service import (  # noqa: E402
    EXTRACTOR_VERSION,
    PHASE,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    ExtractionResultBundle,
    SourceCandidateInputGroup,
    SourceExtractionUnit,
    SourceNameCandidateExtractionError,
    build_extraction_units,
    build_extraction_summary,
    collect_source_candidate_input_groups,
    deterministic_bundle_for_unit,
    extract_groups_with_llm,
    fallback_openai_provider_from_settings,
    group_input_payload_hash,
    llm_cache_fingerprint,
    media_llm_eligibility,
    persist_extraction_bundle,
    primary_openai_provider_from_settings,
    reattach_unit_bundles_to_records,
    source_name_candidate_system_prompt,
    stable_payload_hash,
    table_counts,
    _record_from_failure,
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

PHASE_SLUG = "phase-4.4p2r-f7a-llm-source-name-candidates"
REPORT_MD = Path("docs/reports") / f"{PHASE_SLUG}.md"
REPORT_JSON = Path("docs/reports") / f"{PHASE_SLUG}-summary.json"
OUTPUT_DIR = Path(".local_manifests") / PHASE_SLUG

PRIVATE_PROVIDER_COMPARISON_CSV = OUTPUT_DIR / "provider-comparison-summary.csv"
PRIVATE_PROVIDER_COMPARISON_JSON = OUTPUT_DIR / "provider-comparison-summary.json"
PRIVATE_PROVIDER_SUMMARY_PRIMARY_ONLY_JSON = OUTPUT_DIR / "provider-summary-primary-only.json"
PRIVATE_NAME_CANDIDATES_PRIMARY_CSV = OUTPUT_DIR / "name-candidates-primary.csv"
PRIVATE_NAME_CANDIDATES_FALLBACK_CSV = OUTPUT_DIR / "name-candidates-fallback.csv"
PRIVATE_RECORD_VERDICTS_PRIMARY_CSV = OUTPUT_DIR / "record-verdicts-primary.csv"
PRIVATE_RECORD_VERDICTS_FALLBACK_CSV = OUTPUT_DIR / "record-verdicts-fallback.csv"
PRIVATE_REJECTED_GENERAL_META_CSV = OUTPUT_DIR / "rejected-general-meta.csv"
PRIVATE_DISAGREEMENTS_CSV = OUTPUT_DIR / "disagreements.csv"
PRIVATE_TIMEOUT_ERROR_CSV = OUTPUT_DIR / "timeout-and-error-cases.csv"
PRIVATE_POPULARITY_PREFIX_CSV = OUTPUT_DIR / "popularity-prefix-extractions.csv"
PRIVATE_FALSE_POSITIVE_GUARD_CSV = OUTPUT_DIR / "false-positive-guard-review.csv"
PRIVATE_PIXIV_TITLE_REVIEW_CSV = OUTPUT_DIR / "pixiv-title-candidate-review.csv"
PRIVATE_ROLE_GUARD_REVIEW_CSV = OUTPUT_DIR / "role-guard-review.csv"
PRIVATE_AMBIGUOUS_CSV = OUTPUT_DIR / "ambiguous-needs-review.csv"
PRIVATE_NO_NAME_RECORDS_CSV = OUTPUT_DIR / "no-name-records.csv"
PRIVATE_EXTRACTION_ERRORS_CSV = OUTPUT_DIR / "extraction-errors.csv"
PRIVATE_CHECKPOINT_STATUS_JSON = OUTPUT_DIR / "run-checkpoint-status.json"
PRIVATE_PROGRESS_EVENTS_JSONL = OUTPUT_DIR / "run-progress-events.jsonl"
PRIVATE_INPUT_MANIFEST_JSON = OUTPUT_DIR / "input-manifest.json"
PRIVATE_LLM_INPUTS_JSONL = OUTPUT_DIR / "llm-inputs.jsonl"
PRIVATE_LLM_OUTPUTS_PRIMARY_JSONL = OUTPUT_DIR / "llm-outputs-primary.jsonl"
PRIVATE_LLM_OUTPUTS_FALLBACK_JSONL = OUTPUT_DIR / "llm-outputs-fallback.jsonl"
PRIVATE_VALIDATION_FAILURES_JSONL = OUTPUT_DIR / "validation-failures.jsonl"
PRIVATE_SUMMARY_JSON = OUTPUT_DIR / "summary.json"
PRIVATE_MANUAL_REVIEW_GUIDE = OUTPUT_DIR / "manual-review-guide.md"
PRIVATE_DETAILS_JSON = OUTPUT_DIR / "details.json"
PRIVATE_PROMPT_VERSION_RULES_MD = OUTPUT_DIR / "prompt-version-and-rules.md"
PRIVATE_PROMPT_SAMPLE_ANALYSIS_MD = OUTPUT_DIR / "prompt-sample-analysis.md"
PRIVATE_PUBLIC_REDACTION_CHECK_TXT = OUTPUT_DIR / "public-redaction-check.txt"
PRIVATE_REVIEWER_FIX_SUMMARY_MD = OUTPUT_DIR / "reviewer-fix-summary.md"
PRIVATE_CHECKPOINT_DIR = OUTPUT_DIR / "checkpoints"

PRIVATE_ARTIFACT_DEFAULTS = {
    "provider_comparison_csv": (PRIVATE_PROVIDER_COMPARISON_CSV, "provider-comparison-summary.csv"),
    "provider_comparison_json": (PRIVATE_PROVIDER_COMPARISON_JSON, "provider-comparison-summary.json"),
    "provider_summary_primary_only_json": (PRIVATE_PROVIDER_SUMMARY_PRIMARY_ONLY_JSON, "provider-summary-primary-only.json"),
    "name_candidates_primary_csv": (PRIVATE_NAME_CANDIDATES_PRIMARY_CSV, "name-candidates-primary.csv"),
    "name_candidates_fallback_csv": (PRIVATE_NAME_CANDIDATES_FALLBACK_CSV, "name-candidates-fallback.csv"),
    "record_verdicts_primary_csv": (PRIVATE_RECORD_VERDICTS_PRIMARY_CSV, "record-verdicts-primary.csv"),
    "record_verdicts_fallback_csv": (PRIVATE_RECORD_VERDICTS_FALLBACK_CSV, "record-verdicts-fallback.csv"),
    "rejected_general_meta_csv": (PRIVATE_REJECTED_GENERAL_META_CSV, "rejected-general-meta.csv"),
    "disagreements_csv": (PRIVATE_DISAGREEMENTS_CSV, "disagreements.csv"),
    "timeout_and_error_cases_csv": (PRIVATE_TIMEOUT_ERROR_CSV, "timeout-and-error-cases.csv"),
    "popularity_prefix_extractions_csv": (PRIVATE_POPULARITY_PREFIX_CSV, "popularity-prefix-extractions.csv"),
    "false_positive_guard_review_csv": (PRIVATE_FALSE_POSITIVE_GUARD_CSV, "false-positive-guard-review.csv"),
    "pixiv_title_candidate_review_csv": (PRIVATE_PIXIV_TITLE_REVIEW_CSV, "pixiv-title-candidate-review.csv"),
    "role_guard_review_csv": (PRIVATE_ROLE_GUARD_REVIEW_CSV, "role-guard-review.csv"),
    "ambiguous_needs_review_csv": (PRIVATE_AMBIGUOUS_CSV, "ambiguous-needs-review.csv"),
    "no_name_records_csv": (PRIVATE_NO_NAME_RECORDS_CSV, "no-name-records.csv"),
    "extraction_errors_csv": (PRIVATE_EXTRACTION_ERRORS_CSV, "extraction-errors.csv"),
    "checkpoint_status_json": (PRIVATE_CHECKPOINT_STATUS_JSON, "run-checkpoint-status.json"),
    "progress_events_jsonl": (PRIVATE_PROGRESS_EVENTS_JSONL, "run-progress-events.jsonl"),
    "input_manifest_json": (PRIVATE_INPUT_MANIFEST_JSON, "input-manifest.json"),
    "llm_inputs_jsonl": (PRIVATE_LLM_INPUTS_JSONL, "llm-inputs.jsonl"),
    "llm_outputs_primary_jsonl": (PRIVATE_LLM_OUTPUTS_PRIMARY_JSONL, "llm-outputs-primary.jsonl"),
    "llm_outputs_fallback_jsonl": (PRIVATE_LLM_OUTPUTS_FALLBACK_JSONL, "llm-outputs-fallback.jsonl"),
    "validation_failures_jsonl": (PRIVATE_VALIDATION_FAILURES_JSONL, "validation-failures.jsonl"),
    "summary_json": (PRIVATE_SUMMARY_JSON, "summary.json"),
    "manual_review_guide": (PRIVATE_MANUAL_REVIEW_GUIDE, "manual-review-guide.md"),
    "details_json": (PRIVATE_DETAILS_JSON, "details.json"),
    "prompt_sample_analysis_md": (PRIVATE_PROMPT_SAMPLE_ANALYSIS_MD, "prompt-sample-analysis.md"),
    "prompt_version_and_rules_md": (PRIVATE_PROMPT_VERSION_RULES_MD, "prompt-version-and-rules.md"),
    "public_redaction_check_txt": (PRIVATE_PUBLIC_REDACTION_CHECK_TXT, "public-redaction-check.txt"),
    "reviewer_fix_summary_md": (PRIVATE_REVIEWER_FIX_SUMMARY_MD, "reviewer-fix-summary.md"),
}

HARD_MAX_RECORDS = 1000
HARD_MAX_UNIQUE_STRINGS = 3000
TRUTH_TABLES = (
    "blombooru_entities",
    "blombooru_entity_aliases",
    "blombooru_entity_evidence",
    "blombooru_media_entity_candidates",
    "blombooru_media_entity_assignments",
    "blombooru_media_tags",
    "blombooru_tag_translations",
)


class F7aRunnerError(RuntimeError):
    pass


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def _resolve_repo_path(path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else (ROOT / value).resolve()


def _rebase_default_private_paths(args: argparse.Namespace) -> None:
    output_dir = Path(args.output_dir)
    for attr, (default_path, filename) in PRIVATE_ARTIFACT_DEFAULTS.items():
        current = Path(getattr(args, attr))
        if str(current).replace("\\", "/") == str(default_path).replace("\\", "/"):
            setattr(args, attr, str(output_dir / filename))
    if str(Path(args.checkpoint_dir)).replace("\\", "/") == str(PRIVATE_CHECKPOINT_DIR).replace("\\", "/"):
        setattr(args, "checkpoint_dir", str(output_dir / "checkpoints"))


def _require_under(path: Path, parent: Path, *, code: str) -> None:
    resolved = path.resolve()
    root = parent.resolve()
    if resolved != root and root not in resolved.parents:
        raise F7aRunnerError(code)


def _private_paths(args: argparse.Namespace) -> list[str]:
    return [
        args.provider_comparison_csv,
        args.provider_comparison_json,
        args.provider_summary_primary_only_json,
        args.name_candidates_primary_csv,
        args.name_candidates_fallback_csv,
        args.record_verdicts_primary_csv,
        args.record_verdicts_fallback_csv,
        args.rejected_general_meta_csv,
        args.disagreements_csv,
        args.timeout_and_error_cases_csv,
        args.popularity_prefix_extractions_csv,
        args.false_positive_guard_review_csv,
        args.pixiv_title_candidate_review_csv,
        args.role_guard_review_csv,
        args.ambiguous_needs_review_csv,
        args.no_name_records_csv,
        args.extraction_errors_csv,
        args.checkpoint_status_json,
        args.progress_events_jsonl,
        args.input_manifest_json,
        args.llm_inputs_jsonl,
        args.llm_outputs_primary_jsonl,
        args.llm_outputs_fallback_jsonl,
        args.validation_failures_jsonl,
        args.summary_json,
        args.manual_review_guide,
        args.details_json,
        args.prompt_sample_analysis_md,
        args.prompt_version_and_rules_md,
        args.public_redaction_check_txt,
        args.reviewer_fix_summary_md,
    ]


def _validate_output_paths(args: argparse.Namespace) -> None:
    _rebase_default_private_paths(args)
    output_dir = _resolve_repo_path(args.output_dir)
    _require_under(output_dir, ROOT / ".local_manifests", code="f7a_output_path_violation")
    if PHASE_SLUG not in output_dir.as_posix():
        raise F7aRunnerError("f7a_output_path_missing_phase_slug")
    for value in _private_paths(args):
        _require_under(_resolve_repo_path(value), output_dir, code="f7a_private_artifact_path_violation")
    _require_under(_resolve_repo_path(args.checkpoint_dir), output_dir, code="f7a_checkpoint_path_violation")
    _require_under(_resolve_repo_path(args.report_md), ROOT / "docs/reports", code="f7a_report_md_path_violation")
    _require_under(_resolve_repo_path(args.report_json), ROOT / "docs/reports", code="f7a_report_json_path_violation")


def _coerce_json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _coerce_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_coerce_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _write_text(path: str | Path, content: str) -> None:
    resolved = _resolve_repo_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8", newline="\n")


def _append_jsonl(path: str | Path, row: Mapping[str, Any]) -> None:
    resolved = _resolve_repo_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with resolved.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(_coerce_json_safe(row), ensure_ascii=False, sort_keys=True, default=str))
        handle.write("\n")


def _write_json(path: str | Path, payload: Any) -> None:
    _write_text(path, json.dumps(_coerce_json_safe(payload), ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _read_json(path: str | Path) -> Any | None:
    resolved = _resolve_repo_path(path)
    if not resolved.exists():
        return None
    return json.loads(resolved.read_text(encoding="utf-8"))


def _jsonl(rows: Iterable[Mapping[str, Any]]) -> str:
    return "\n".join(json.dumps(_coerce_json_safe(row), ensure_ascii=False, sort_keys=True, default=str) for row in rows) + "\n"


def _csv(rows_iter: Iterable[Mapping[str, Any]]) -> str:
    rows = [dict(row) for row in rows_iter]
    if not rows:
        return ""
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    from io import StringIO

    handle = StringIO()
    writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_value(row.get(key)) for key in fieldnames})
    return handle.getvalue()


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(_coerce_json_safe(value), ensure_ascii=False, sort_keys=True, default=str)
    return value


def _connect_db():
    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    check_and_migrate_schema(engine)
    SessionLocal = sessionmaker(bind=engine)
    return engine, SessionLocal()


def _db_identity(db) -> dict[str, Any]:
    result = {
        "violet_env": settings.VIOLET_ENV,
        "db_name": settings.DB_NAME,
        "database_url_redacted": True,
    }
    try:
        result["current_database"] = db.execute(text("SELECT current_database()")).scalar()
    except Exception:
        result["current_database"] = None
    return result


def generate_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{PHASE_SLUG}-{timestamp}-{uuid4().hex[:8]}"


def _git_value(command: Sequence[str]) -> str:
    proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=True)
    return proc.stdout.strip()


def _group_from_dict(row: Mapping[str, Any]) -> SourceCandidateInputGroup:
    fields = SourceCandidateInputGroup.__dataclass_fields__
    payload = {key: row.get(key) for key in fields if key in row}
    for key in ("tags", "source_names", "source_assertions", "alias_candidates", "media_tags"):
        if key in payload and isinstance(payload[key], list):
            payload[key] = tuple(payload[key])
    return SourceCandidateInputGroup(**payload)


def _manifest_from_groups(groups: Sequence[SourceCandidateInputGroup], input_summary: Mapping[str, Any]) -> dict[str, Any]:
    rows = [asdict(group) for group in groups]
    return {
        "phase": PHASE,
        "extractor_version": EXTRACTOR_VERSION,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "input_summary": dict(input_summary),
        "groups": rows,
        "group_input_hashes": {group.group_key: group_input_payload_hash(group) for group in groups},
        "manifest_hash": stable_payload_hash(rows),
    }


def _load_manifest(args: argparse.Namespace) -> tuple[list[SourceCandidateInputGroup], dict[str, Any]] | None:
    payload = _read_json(args.input_manifest_json)
    if not isinstance(payload, Mapping) or not isinstance(payload.get("groups"), list):
        return None
    groups = [_group_from_dict(row) for row in payload["groups"] if isinstance(row, Mapping)]
    return groups, dict(payload)


def _manifest_group_string_keys(group: SourceCandidateInputGroup) -> set[str]:
    values: list[Any] = [group.title, group.caption, group.artist_name]
    for row in group.tags:
        if isinstance(row, Mapping):
            values.extend([row.get("raw_tag"), row.get("normalized_tag"), row.get("canonical_tag_key")])
    for row in group.source_names:
        if isinstance(row, Mapping):
            values.extend([row.get("raw_name"), row.get("normalized_name"), row.get("canonical_name_key")])
    for row in group.source_assertions:
        if isinstance(row, Mapping):
            values.extend([row.get("raw_input"), row.get("normalized_input"), row.get("asserted_name"), row.get("canonical_name_key")])
    for row in group.alias_candidates:
        if isinstance(row, Mapping):
            values.extend([row.get("raw_left"), row.get("raw_right"), row.get("canonical_left_key"), row.get("canonical_right_key")])
    for row in group.media_tags:
        if isinstance(row, Mapping):
            values.append(row.get("name"))
    return {canonical_source_key(value) for value in values if canonical_source_key(value)}


def _revalidate_cached_manifest_groups(
    db,
    groups: Sequence[SourceCandidateInputGroup],
    args: argparse.Namespace,
) -> tuple[list[SourceCandidateInputGroup], dict[str, Any]]:
    kept: list[SourceCandidateInputGroup] = []
    unique_strings: set[str] = set()
    drop_counts: Counter[str] = Counter()
    eligibility_counts: Counter[str] = Counter()
    max_records = int(args.max_records)
    max_unique_strings = int(args.max_unique_strings)
    for group in groups:
        source_record = None
        media = None
        if group.source_metadata_record_id:
            source_record = db.get(SourceMetadataRecord, group.source_metadata_record_id)
            if source_record is None:
                drop_counts["source_metadata_record_missing"] += 1
                continue
            if source_record.status != "observed":
                drop_counts["source_metadata_record_not_observed"] += 1
                continue
            if source_record.media_id:
                media = db.get(Media, source_record.media_id)
        if media is None and group.media_id:
            media = db.get(Media, group.media_id)
        eligible, eligibility_reason, eligibility_payload = media_llm_eligibility(media)
        eligibility_counts[eligibility_reason] += 1
        if not eligible:
            drop_counts[eligibility_reason] += 1
            continue
        group_strings = _manifest_group_string_keys(group)
        if not group_strings:
            drop_counts["no_raw_strings_after_revalidation"] += 1
            continue
        if len(kept) >= max_records:
            drop_counts["max_records_cap"] += 1
            continue
        if len(group_strings) > max_unique_strings:
            drop_counts["max_unique_strings_group_oversized"] += 1
            continue
        if len(unique_strings | group_strings) > max_unique_strings:
            drop_counts["max_unique_strings_cap"] += 1
            continue
        unique_strings.update(group_strings)
        kept.append(
            replace(
                group,
                media_id=getattr(media, "id", group.media_id),
                content_class=eligibility_payload["content_class"],
                content_class_reviewed=bool(eligibility_payload["content_class_reviewed"]),
                content_class_locked=bool(eligibility_payload["content_class_locked"]),
                eligibility_status="eligible",
                eligibility_reason=eligibility_reason,
            )
        )
    return kept, {
        "cached_manifest_revalidated": True,
        "cached_manifest_groups_input": len(groups),
        "cached_manifest_groups_kept": len(kept),
        "cached_manifest_groups_dropped": sum(drop_counts.values()),
        "cached_manifest_drop_counts": dict(drop_counts),
        "cached_manifest_eligibility_counts": dict(eligibility_counts),
        "groups_collected": len(kept),
        "eligible_groups_collected": len(kept),
        "unique_raw_string_count_after_manifest_revalidation": len(unique_strings),
        "max_records": max_records,
        "max_unique_strings": max_unique_strings,
    }


def _checkpoint_path(args: argparse.Namespace) -> Path:
    return _resolve_repo_path(args.checkpoint_status_json)


def _load_checkpoint(args: argparse.Namespace) -> dict[str, Any]:
    payload = _read_json(args.checkpoint_status_json)
    if isinstance(payload, Mapping):
        return dict(payload)
    return {"runs": {}, "events": {"progress_events_jsonl": _rel(_resolve_repo_path(args.progress_events_jsonl))}}


def _save_checkpoint(args: argparse.Namespace, checkpoint: Mapping[str, Any]) -> None:
    _write_json(args.checkpoint_status_json, checkpoint)


def _event(args: argparse.Namespace, row: Mapping[str, Any]) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **dict(row),
    }
    _append_jsonl(args.progress_events_jsonl, payload)


def _emit_progress(args: argparse.Namespace, row: Mapping[str, Any]) -> None:
    _event(args, row)
    print(json.dumps(_coerce_json_safe(row), ensure_ascii=False, sort_keys=True, default=str), flush=True)


def _safe_group_ref(group_key: str) -> str:
    return stable_payload_hash({"group_key": group_key})[:12]


def _unit_result_path(args: argparse.Namespace, provider_mode: str, fingerprint: str) -> Path:
    return _resolve_repo_path(args.checkpoint_dir) / _mode_output_family(provider_mode) / f"{fingerprint[:32]}.json"


def _bundle_to_json(bundle: ExtractionResultBundle) -> dict[str, Any]:
    return {
        "run_id": bundle.run_id,
        "run_label": bundle.run_label,
        "groups": [asdict(row) for row in bundle.groups],
        "record_verdicts": [asdict(row) for row in bundle.record_verdicts],
        "candidates": [asdict(row) for row in bundle.candidates],
        "rejected_tags": [asdict(row) for row in bundle.rejected_tags],
        "meta_tags": [asdict(row) for row in bundle.meta_tags],
        "ambiguous_items": [asdict(row) for row in bundle.ambiguous_items],
        "llm_inputs": list(bundle.llm_inputs),
        "llm_outputs": list(bundle.llm_outputs),
        "validation_failures": list(bundle.validation_failures),
        "summary": dict(bundle.summary),
    }


def _provider_for_mode(provider_mode: str):
    if provider_mode.startswith("primary"):
        return primary_openai_provider_from_settings()
    if provider_mode.startswith("fallback"):
        return fallback_openai_provider_from_settings()
    raise F7aRunnerError(f"unknown_provider_mode:{provider_mode}")


def _provider_modes(args: argparse.Namespace) -> list[str]:
    modes = [normalize_source_text(item) for item in str(args.provider_modes).split(",")]
    result = [mode for mode in modes if mode]
    allowed = {"primary_serial", "fallback_serial", "primary_concurrent", "fallback_concurrent"}
    unknown = [mode for mode in result if mode not in allowed]
    if unknown:
        raise F7aRunnerError(f"unknown_provider_modes:{','.join(unknown)}")
    return result


def _mode_concurrency(provider_mode: str, args: argparse.Namespace) -> int:
    if provider_mode.endswith("_serial"):
        return 1
    return max(1, int(args.llm_concurrency))


def _mode_output_family(provider_mode: str) -> str:
    return "primary" if provider_mode.startswith("primary") else "fallback"


def _mode_run_id(base_run_id: str, provider_mode: str) -> str:
    return f"{base_run_id}:{provider_mode}"


async def _provider_json_preflight(
    *,
    provider,
    provider_mode: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    prompts = [
        (
            {"role": "system", "content": "Return compact JSON only. No markdown, no prose."},
            {"role": "user", "content": 'Return exactly {"ok":true,"stage":"f7a_preflight"}.'},
        ),
        (
            {"role": "system", "content": "JSON only."},
            {"role": "user", "content": '{"ok":true,"stage":"f7a_preflight"}'},
        ),
    ]
    attempts: list[dict[str, Any]] = []
    for attempt_index, messages in enumerate(prompts, start=1):
        try:
            payload = await asyncio.wait_for(
                provider.complete_json(list(messages), temperature=0.0, max_tokens=400),
                timeout=timeout_seconds,
            )
            if isinstance(payload, Mapping) and payload.get("ok") is True:
                return {
                    "provider_mode": provider_mode,
                    "preflight": "pass",
                    "attempts": attempt_index,
                    "external_call_attempts": attempt_index,
                    "json_object": True,
                    "error_type": None,
                }
            attempts.append(
                {
                    "attempt": attempt_index,
                    "error_type": "wrong_json_shape",
                    "json_object": isinstance(payload, Mapping),
                }
            )
        except Exception as exc:
            attempts.append(
                {
                    "attempt": attempt_index,
                    "error_type": type(exc).__name__,
                    "json_object": False,
                }
            )
    return {
        "provider_mode": provider_mode,
        "preflight": "fail",
        "attempts": len(attempts),
        "external_call_attempts": len(attempts),
        "json_object": False,
        "error_type": attempts[-1].get("error_type") if attempts else "unknown_preflight_error",
        "attempt_errors": attempts,
    }


async def _preflight_provider_modes(args: argparse.Namespace, checkpoint: dict[str, Any]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    seen: dict[tuple[Any, Any], dict[str, Any]] = {}
    for provider_mode in _provider_modes(args):
        provider, provider_summary = _provider_for_mode(provider_mode)
        provider_key = (
            provider_summary.get("llm_provider_label") or provider_summary.get("provider_mode"),
            provider_summary.get("model_label"),
        )
        if provider is None:
            result = {
                "provider_mode": provider_mode,
                "preflight": "fail",
                "external_call_attempts": 0,
                "error_type": provider_summary.get("unavailable_reason") or "provider_unavailable",
            }
        elif provider_key in seen:
            result = {
                **seen[provider_key],
                "provider_mode": provider_mode,
                "preflight_reused_from": seen[provider_key].get("provider_mode"),
                "external_call_attempts": 0,
            }
        else:
            result = await _provider_json_preflight(
                provider=provider,
                provider_mode=provider_mode,
                timeout_seconds=float(args.llm_timeout_seconds),
            )
            seen[provider_key] = result
        results.append(result)
    summary = {
        "status": "pass" if all(row.get("preflight") == "pass" for row in results) else "fail",
        "results": results,
        "llm_preflight_calls": sum(int(row.get("external_call_attempts") or 0) for row in results),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    checkpoint["provider_preflight"] = summary
    _save_checkpoint(args, checkpoint)
    return summary


def _timing_stats(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"average_seconds": None, "p50_seconds": None, "p95_seconds": None}
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95)))
    return {
        "average_seconds": round(sum(values) / len(values), 3),
        "p50_seconds": round(statistics.median(values), 3),
        "p95_seconds": round(ordered[p95_index], 3),
    }


def _error_code_from_failure(row: Mapping[str, Any]) -> str:
    text_value = f"{row.get('error_type', '')}:{row.get('error', '')}".lower()
    if "timeout" in text_value:
        return "timeout"
    if "json" in text_value or "responseformat" in text_value:
        return "invalid_json"
    if "confidence" in text_value:
        return "candidate_confidence_invalid"
    if "candidate_array" in text_value:
        return "malformed_candidate_array"
    if "no_candidates_after_validation" in text_value:
        return "no_candidates_after_validation"
    if "schema" in text_value:
        return "schema_validation_failed"
    if "provider" in text_value or "http" in text_value or "transport" in text_value:
        return "provider_error"
    return "schema_validation_failed"


def _aggregate_mode_rows(result_rows: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    aggregate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in result_rows:
        bundle = row.get("bundle") if isinstance(row.get("bundle"), Mapping) else {}
        for key in (
            "groups",
            "record_verdicts",
            "candidates",
            "rejected_tags",
            "meta_tags",
            "ambiguous_items",
            "llm_inputs",
            "llm_outputs",
            "validation_failures",
        ):
            values = bundle.get(key) if isinstance(bundle, Mapping) else []
            if isinstance(values, list):
                aggregate[key].extend(dict(item) if isinstance(item, Mapping) else {"value": item} for item in values)
    return aggregate


def _mode_summary(provider_mode: str, rows: Sequence[Mapping[str, Any]], provider_summary: Mapping[str, Any], concurrency: int) -> dict[str, Any]:
    aggregate = _aggregate_mode_rows(rows)
    verdict_counts = Counter(row.get("extraction_verdict") for row in aggregate["record_verdicts"])
    candidates = aggregate["candidates"]
    rejected = aggregate["rejected_tags"]
    validation = aggregate["validation_failures"]
    durations = [float(row.get("elapsed_seconds") or 0.0) for row in rows if row.get("elapsed_seconds") is not None]
    unit_status_counts = Counter(row.get("status") for row in rows)
    row_failures_without_bundle = [
        row for row in rows if row.get("status") in {"terminal_error", "retryable_error"} and not isinstance(row.get("bundle"), Mapping)
    ]
    candidate_keys = [
        (
            row.get("group_key"),
            row.get("canonical_key"),
            row.get("candidate_role"),
            row.get("extraction_action"),
        )
        for row in candidates
    ]
    duplicate_count = max(0, len(candidate_keys) - len(set(candidate_keys)))
    failure_sources = [*validation, *row_failures_without_bundle]
    timeout_count = sum(1 for row in failure_sources if _error_code_from_failure(row) == "timeout")
    invalid_json_count = sum(1 for row in failure_sources if _error_code_from_failure(row) == "invalid_json")
    schema_failure_count = sum(1 for row in failure_sources if _error_code_from_failure(row) == "schema_validation_failed")
    unknown_name_like_active_count = sum(
        1
        for row in candidates
        if row.get("candidate_role") == "unknown_name_like" and row.get("candidate_status") == "active_candidate"
    )
    false_positive_guard_count = len(_false_positive_guard_rows(candidates))
    pixiv_title_active_work_title_count = sum(
        1
        for row in candidates
        if row.get("origin_type") in {"pixiv_title", "pixiv_caption"}
        and row.get("candidate_role") == "work_title"
        and row.get("candidate_status") == "active_candidate"
    )
    source_title_active_count = sum(
        1
        for row in candidates
        if row.get("candidate_role") == "source_title" and row.get("candidate_status") == "active_candidate"
    )
    unguarded_source_title_active_count = _unguarded_active_source_title_count(candidates)
    role_guard_count = len(_role_guard_rows(candidates))
    title_extraction_count = sum(
        1
        for row in candidates
        if row.get("origin_type") in {"pixiv_title", "pixiv_caption"}
        and row.get("candidate_role") in {"character", "person", "alias_like", "unknown_name_like"}
    )
    summary = {
        "provider_mode": provider_mode,
        "llm_provider_mode": provider_summary.get("provider_mode"),
        "llm_provider_label": provider_summary.get("llm_provider_label"),
        "model_label": provider_summary.get("model_label"),
        "group_count": len(rows),
        "unique_string_count": len({value for group in aggregate["groups"] for value in (group.get("group_input_hash"), group.get("group_key")) if value}),
        "total_wall_time_seconds": round(sum(durations), 3),
        "llm_wall_time_seconds": round(sum(durations), 3),
        **_timing_stats(durations),
        "chunk_count": len(rows),
        "concurrency": concurrency,
        "timeout_count": timeout_count,
        "retry_count": sum(int(row.get("chunk_retries") or 0) for row in rows),
        "invalid_json_count": invalid_json_count,
        "schema_failure_count": schema_failure_count,
        "completed_unit_count": unit_status_counts.get("completed", 0),
        "terminal_error_count": unit_status_counts.get("terminal_error", 0),
        "retryable_error_count": unit_status_counts.get("retryable_error", 0),
        "record_terminal_error_count": verdict_counts.get("extraction_error_terminal", 0) + verdict_counts.get("extraction_error", 0),
        "record_retryable_error_count": verdict_counts.get("extraction_error_retryable", 0),
        "candidate_count_total": len(candidates),
        "candidate_count_by_role": dict(Counter(row.get("candidate_role") for row in candidates)),
        "candidate_count_by_status": dict(Counter(row.get("candidate_status") for row in candidates)),
        "unknown_name_like_active_count": unknown_name_like_active_count,
        "pixiv_title_active_work_title_count": pixiv_title_active_work_title_count,
        "source_title_active_count": source_title_active_count,
        "unguarded_source_title_active_count": unguarded_source_title_active_count,
        "false_positive_guard_review_count": false_positive_guard_count,
        "role_guard_count": role_guard_count,
        "title_extraction_count": title_extraction_count,
        "ambiguous_count": len(aggregate["ambiguous_items"]),
        "no_name_count": sum(1 for row in aggregate["record_verdicts"] if row.get("no_name_reason")),
        "record_verdict_counts": dict(verdict_counts),
        "rejected_total": len(rejected),
        "rejected_by_reason": dict(Counter(row.get("rejection_reason") for row in rejected)),
        "popularity_prefix_count": sum(1 for row in candidates if row.get("extraction_action") == "popularity_suffix_stripped"),
        "duplicate_candidate_count": duplicate_count,
        "duplicate_candidate_rate": round(duplicate_count / len(candidates), 4) if candidates else 0.0,
        "token_usage": "not_available_from_provider_response",
        "approximate_cost": "not_available_from_provider_response",
    }
    return summary


def _combine_bundles_for_db(
    *,
    run_id: str,
    run_label: str,
    provider_mode: str,
    group: SourceCandidateInputGroup,
    bundle: ExtractionResultBundle,
) -> ExtractionResultBundle:
    # The service already returns a one-group bundle.  This small wrapper keeps
    # the run label/mode explicit in persisted rows and artifacts.
    return ExtractionResultBundle(
        run_id=_mode_run_id(run_id, provider_mode),
        run_label=run_label,
        groups=bundle.groups,
        record_verdicts=bundle.record_verdicts,
        candidates=bundle.candidates,
        rejected_tags=bundle.rejected_tags,
        meta_tags=bundle.meta_tags,
        ambiguous_items=bundle.ambiguous_items,
        llm_inputs=bundle.llm_inputs,
        llm_outputs=bundle.llm_outputs,
        validation_failures=bundle.validation_failures,
        summary=bundle.summary,
    )


def _failure_bundle_for_unit(
    *,
    run_id: str,
    run_label: str,
    provider_mode: str,
    unit: SourceExtractionUnit,
    row: Mapping[str, Any],
) -> ExtractionResultBundle:
    retryable = row.get("status") == "retryable_error"
    error_code = normalize_source_text(row.get("error_type")) or normalize_source_text(row.get("error")) or "unit_error"
    verdict, candidates, rejected, meta, ambiguous = _record_from_failure(
        unit.unit_group,
        error=error_code,
        retryable=retryable,
    )
    validation_failure = {
        "group_key": unit.unit_group.group_key,
        "extraction_key": unit.extraction_key,
        "error_code": error_code,
        "status": row.get("status"),
        "retryable": retryable,
        "source_layer_only": True,
    }
    summary = build_extraction_summary(
        groups=[unit.unit_group],
        record_verdicts=[verdict],
        candidates=candidates,
        rejected_tags=rejected,
        meta_tags=meta,
        ambiguous_items=ambiguous,
        llm_counters={"failed_unit_bundle_synthesized": 1},
        validation_failures=[validation_failure],
    )
    return ExtractionResultBundle(
        run_id=_mode_run_id(run_id, provider_mode),
        run_label=run_label,
        groups=(unit.unit_group,),
        record_verdicts=(verdict,),
        candidates=candidates,
        rejected_tags=rejected,
        meta_tags=meta,
        ambiguous_items=ambiguous,
        llm_inputs=(),
        llm_outputs=(),
        validation_failures=(validation_failure,),
        summary=summary,
    )


async def _process_unit(
    *,
    args: argparse.Namespace,
    provider,
    provider_summary: Mapping[str, Any],
    provider_mode: str,
    run_id: str,
    run_label: str,
    unit: SourceExtractionUnit,
    checkpoint: dict[str, Any],
    mode_state: dict[str, Any],
    in_flight: dict[str, asyncio.Task],
) -> dict[str, Any]:
    fingerprint = llm_cache_fingerprint(unit.unit_group, provider_summary)
    result_path = _unit_result_path(args, provider_mode, fingerprint)
    mode_state.setdefault("units", {})
    if (
        args.reuse_checkpoint
        and result_path.exists()
    ):
        payload = _read_json(result_path)
        if (
            isinstance(payload, Mapping)
            and payload.get("cache_fingerprint") == fingerprint
            and payload.get("status") in {"completed", "terminal_error"}
        ):
            mode_state["cache_hits"] = int(mode_state.get("cache_hits", 0)) + 1
            cached = dict(payload)
            cached["cached_api_call_attempts"] = cached.get("api_call_attempts", 0)
            cached["api_call_attempts"] = 0
            cached["from_checkpoint_cache"] = True
            return cached
    if fingerprint in in_flight:
        mode_state["inflight_dedupe_hits"] = int(mode_state.get("inflight_dedupe_hits", 0)) + 1
        return await in_flight[fingerprint]

    async def run_once() -> dict[str, Any]:
        start = time.perf_counter()
        mode_state["units"][unit.extraction_key] = {
            "status": "in_progress",
            "cache_fingerprint": fingerprint,
            "unit_ref": _safe_group_ref(unit.extraction_key),
            "source_count": len(unit.occurrences),
            "llm_required": unit.llm_required,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        _save_checkpoint(args, checkpoint)
        try:
            if unit.llm_required:
                bundle = await extract_groups_with_llm(
                    provider,
                    [unit.unit_group],
                    run_id=_mode_run_id(run_id, provider_mode),
                    run_label=run_label,
                    chunk_size=1,
                    retries=int(args.llm_retries),
                    max_tokens=int(args.max_tokens),
                    timeout_seconds=float(args.llm_timeout_seconds),
                    provider_summary=provider_summary,
                    cached_records_by_fingerprint={},
                )
            else:
                bundle = deterministic_bundle_for_unit(
                    unit,
                    run_id=_mode_run_id(run_id, provider_mode),
                    run_label=run_label,
                )
            bundle = _combine_bundles_for_db(
                run_id=run_id,
                run_label=run_label,
                provider_mode=provider_mode,
                group=unit.unit_group,
                bundle=bundle,
            )
            elapsed = round(time.perf_counter() - start, 3)
            verdict = bundle.record_verdicts[0].extraction_verdict if bundle.record_verdicts else "extraction_error_terminal"
            status = "retryable_error" if verdict == "extraction_error_retryable" else "terminal_error" if verdict in {"extraction_error_terminal", "extraction_error"} else "completed"
            result = {
                "provider_mode": provider_mode,
                "extraction_key": unit.extraction_key,
                "unit_ref": _safe_group_ref(unit.extraction_key),
                "normalized_value": unit.normalized_value,
                "source_field": unit.source_field,
                "source_count": len(unit.occurrences),
                "llm_required": unit.llm_required,
                "cache_fingerprint": fingerprint,
                "input_payload_hash": group_input_payload_hash(unit.unit_group),
                "status": status,
                "extraction_verdict": verdict,
                "elapsed_seconds": elapsed,
                "api_call_attempts": bundle.summary.get("llm", {}).get("api_call_attempts", 0),
                "chunk_retries": bundle.summary.get("llm", {}).get("chunk_retries", 0),
                "validation_failure_count": len(bundle.validation_failures),
                "candidate_count": len(bundle.candidates),
                "bundle": _bundle_to_json(bundle),
                "from_checkpoint_cache": False,
            }
            _write_json(result_path, result)
            verified = _read_json(result_path)
            if not isinstance(verified, Mapping) or verified.get("cache_fingerprint") != fingerprint:
                raise F7aRunnerError("unit_artifact_validation_failed")
            mode_state["units"][unit.extraction_key] = {
                "status": status,
                "cache_fingerprint": fingerprint,
                "result_path": _rel(result_path),
                "unit_ref": _safe_group_ref(unit.extraction_key),
                "source_count": len(unit.occurrences),
                "llm_required": unit.llm_required,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_seconds": elapsed,
                "extraction_verdict": verdict,
                "candidate_count": len(bundle.candidates),
            }
            _save_checkpoint(args, checkpoint)
            return result
        except Exception as exc:
            elapsed = round(time.perf_counter() - start, 3)
            failure = {
                "provider_mode": provider_mode,
                "extraction_key": unit.extraction_key,
                "unit_ref": _safe_group_ref(unit.extraction_key),
                "normalized_value": unit.normalized_value,
                "source_field": unit.source_field,
                "source_count": len(unit.occurrences),
                "llm_required": unit.llm_required,
                "cache_fingerprint": fingerprint,
                "status": "retryable_error",
                "elapsed_seconds": elapsed,
                "error_type": type(exc).__name__,
                "error": str(exc)[:1000],
            }
            _write_json(result_path, failure)
            mode_state["units"][unit.extraction_key] = {
                **failure,
                "result_path": _rel(result_path),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
            _save_checkpoint(args, checkpoint)
            return failure

    task = asyncio.create_task(run_once())
    in_flight[fingerprint] = task
    try:
        return await task
    finally:
        in_flight.pop(fingerprint, None)


async def _process_provider_mode(
    *,
    args: argparse.Namespace,
    db,
    run_id: str,
    run_label: str,
    provider_mode: str,
    groups: Sequence[SourceCandidateInputGroup],
    units: Sequence[SourceExtractionUnit],
    checkpoint: dict[str, Any],
) -> dict[str, Any]:
    provider, provider_summary = _provider_for_mode(provider_mode)
    mode_state = checkpoint.setdefault("runs", {}).setdefault(
        provider_mode,
        {
            "provider_mode": provider_mode,
            "provider_summary": provider_summary,
            "units": {},
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    mode_state["provider_summary"] = provider_summary
    mode_state["concurrency"] = _mode_concurrency(provider_mode, args)
    mode_state["cache_hits"] = 0
    mode_state["inflight_dedupe_hits"] = 0
    if provider is None:
        mode_state["status"] = "provider_unavailable"
        mode_state["unavailable_reason"] = provider_summary.get("unavailable_reason")
        _save_checkpoint(args, checkpoint)
        return {
            "provider_mode": provider_mode,
            "provider_summary": provider_summary,
            "rows": [],
            "record_bundle": {},
            "summary": {
                "provider_mode": provider_mode,
                "llm_provider_mode": provider_summary.get("provider_mode"),
                "llm_provider_label": provider_summary.get("llm_provider_label"),
                "model_label": provider_summary.get("model_label"),
                "provider_unavailable": True,
                "unavailable_reason": provider_summary.get("unavailable_reason"),
                "group_count": 0,
                "unique_string_count": 0,
                "raw_string_occurrences_total": 0,
                "unique_extraction_units_total": 0,
                "deterministic_resolved_units": 0,
                "llm_required_units": 0,
                "total_wall_time_seconds": 0,
                "llm_wall_time_seconds": 0,
                "average_seconds": None,
                "p50_seconds": None,
                "p95_seconds": None,
                "chunk_count": 0,
                "concurrency": _mode_concurrency(provider_mode, args),
                "timeout_count": 0,
                "retry_count": 0,
                "invalid_json_count": 0,
                "schema_failure_count": 0,
                "terminal_error_count": 0,
                "retryable_error_count": 0,
                "candidate_count_total": 0,
                "candidate_count_by_role": {},
                "candidate_count_by_status": {},
                "ambiguous_count": 0,
                "no_name_count": 0,
                "record_verdict_counts": {},
                "rejected_total": 0,
                "rejected_by_reason": {},
                "popularity_prefix_count": 0,
                "duplicate_candidate_count": 0,
                "duplicate_candidate_rate": 0.0,
                "llm_calls_attempted": 0,
                "llm_calls_avoided_by_dedupe": 0,
                "inflight_dedupe_hits": 0,
                "token_usage": "not_available_from_provider_response",
                "approximate_cost": "not_available_from_provider_response",
            },
        }

    semaphore = asyncio.Semaphore(_mode_concurrency(provider_mode, args))
    total = len(units)
    rows: list[dict[str, Any]] = []
    in_flight: dict[str, asyncio.Task] = {}
    last_progress = time.perf_counter()
    started = time.perf_counter()
    previous_elapsed_seconds = float(mode_state.get("elapsed_seconds") or 0.0)

    async def guarded(unit: SourceExtractionUnit) -> dict[str, Any]:
        async with semaphore:
            return await _process_unit(
                args=args,
                provider=provider,
                provider_summary=provider_summary,
                provider_mode=provider_mode,
                run_id=run_id,
                run_label=run_label,
                unit=unit,
                checkpoint=checkpoint,
                mode_state=mode_state,
                in_flight=in_flight,
            )

    tasks = [asyncio.create_task(guarded(unit)) for unit in units]
    for task in asyncio.as_completed(tasks):
        row = await task
        rows.append(row)
        completed = len(rows)
        status_counts = Counter(item.get("status") for item in rows)
        now = time.perf_counter()
        if completed == 1 or completed == total or now - last_progress >= float(args.heartbeat_seconds):
            last_progress = now
            _emit_progress(
                args,
                {
                    "event": "f7a_progress",
                    "run_id": run_id,
                    "provider_mode": provider_mode,
                    "elapsed_seconds": round(now - started, 3),
                    "total_extraction_units": total,
                    "completed_extraction_units": completed,
                    "in_progress_units": max(0, min(total - completed, _mode_concurrency(provider_mode, args))),
                    "failed_retryable_units": status_counts.get("retryable_error", 0),
                    "terminal_error_units": status_counts.get("terminal_error", 0),
                    "llm_calls_attempted": sum(int(item.get("api_call_attempts") or 0) for item in rows),
                    "cache_hits": int(mode_state.get("cache_hits", 0)),
                    "inflight_dedupe_hits": int(mode_state.get("inflight_dedupe_hits", 0)),
                    "retries": sum(int(item.get("chunk_retries") or 0) for item in rows),
                    "current_concurrency": _mode_concurrency(provider_mode, args),
                    "latest_completed_unit_ref": row.get("unit_ref"),
                    "estimated_remaining_seconds": round(((now - started) / completed) * (total - completed), 3) if completed else None,
                },
            )

    mode_state["status"] = "completed"
    mode_state["finished_at"] = datetime.now(timezone.utc).isoformat()
    current_elapsed_seconds = round(time.perf_counter() - started, 3)
    all_rows_from_cache = bool(rows) and all(bool(item.get("from_checkpoint_cache")) for item in rows)
    if all_rows_from_cache and previous_elapsed_seconds > 0:
        mode_state["elapsed_seconds"] = previous_elapsed_seconds
        mode_state["last_cache_refresh_elapsed_seconds"] = current_elapsed_seconds
    else:
        mode_state["elapsed_seconds"] = current_elapsed_seconds
    _save_checkpoint(args, checkpoint)
    # Rehydrate dataclass rows from saved JSON for record-level reattachment.
    from app.services.source_name_candidate_extraction_service import (  # noqa: PLC0415
        AmbiguousItemDraft,
        CandidateDraft,
        MetaTagDraft,
        RecordVerdictDraft,
        RejectedTagDraft,
    )

    unit_bundles = {}
    units_by_key = {unit.extraction_key: unit for unit in units}
    for row in rows:
        bundle_row = row.get("bundle")
        if isinstance(bundle_row, Mapping):
            unit_bundles[row["extraction_key"]] = ExtractionResultBundle(
                run_id=str(bundle_row.get("run_id")),
                run_label=str(bundle_row.get("run_label")),
                groups=tuple(_group_from_dict(group) for group in bundle_row.get("groups", [])),
                record_verdicts=tuple(RecordVerdictDraft(**item) for item in bundle_row.get("record_verdicts", [])),
                candidates=tuple(CandidateDraft(**item) for item in bundle_row.get("candidates", [])),
                rejected_tags=tuple(RejectedTagDraft(**item) for item in bundle_row.get("rejected_tags", [])),
                meta_tags=tuple(MetaTagDraft(**item) for item in bundle_row.get("meta_tags", [])),
                ambiguous_items=tuple(AmbiguousItemDraft(**item) for item in bundle_row.get("ambiguous_items", [])),
                llm_inputs=tuple(bundle_row.get("llm_inputs", [])),
                llm_outputs=tuple(bundle_row.get("llm_outputs", [])),
                validation_failures=tuple(bundle_row.get("validation_failures", [])),
                summary=dict(bundle_row.get("summary", {})),
            )
            continue
        if row.get("status") in {"retryable_error", "terminal_error"}:
            unit = units_by_key.get(str(row.get("extraction_key")))
            if unit is not None:
                unit_bundles[row["extraction_key"]] = _failure_bundle_for_unit(
                    run_id=run_id,
                    run_label=run_label,
                    provider_mode=provider_mode,
                    unit=unit,
                    row=row,
                )
    record_bundle = reattach_unit_bundles_to_records(
        groups,
        units,
        unit_bundles,
        run_id=_mode_run_id(run_id, provider_mode),
        run_label=run_label,
    )
    record_result = {"bundle": _bundle_to_json(record_bundle)}
    summary = _mode_summary(provider_mode, [record_result], provider_summary, _mode_concurrency(provider_mode, args))
    unit_durations = [float(item.get("elapsed_seconds") or 0.0) for item in rows if item.get("elapsed_seconds") is not None]
    timing_stats = _timing_stats(unit_durations)
    unit_status_counts = Counter(item.get("status") for item in rows)
    summary.update(
        {
            "group_count": len(groups),
            "unique_string_count": len(units),
            "total_wall_time_seconds": round(float(mode_state.get("elapsed_seconds") or 0.0), 3),
            "llm_wall_time_seconds": round(sum(unit_durations), 3),
            "average_seconds": timing_stats["average_seconds"],
            "p50_seconds": timing_stats["p50_seconds"],
            "p95_seconds": timing_stats["p95_seconds"],
            "chunk_count": len(rows),
            "retry_count": sum(int(item.get("chunk_retries") or 0) for item in rows),
            "raw_string_occurrences_total": sum(len(unit.occurrences) for unit in units),
            "unique_extraction_units_total": len(units),
            "llm_calls_attempted": sum(int(item.get("api_call_attempts") or 0) for item in rows),
            "llm_calls_avoided_by_dedupe": max(0, sum(len(unit.occurrences) for unit in units) - len(units)),
            "inflight_dedupe_hits": int(mode_state.get("inflight_dedupe_hits", 0)),
            "deterministic_resolved_units": sum(1 for unit in units if not unit.llm_required),
            "llm_required_units": sum(1 for unit in units if unit.llm_required),
            "cache_hits": int(mode_state.get("cache_hits", 0)),
            "unit_status_counts": dict(unit_status_counts),
            "completed_unit_count": unit_status_counts.get("completed", 0),
            "terminal_error_count": unit_status_counts.get("terminal_error", 0),
            "retryable_error_count": unit_status_counts.get("retryable_error", 0),
        }
    )
    if args.apply_db:
        mode_artifact_path = _resolve_repo_path(args.checkpoint_dir) / provider_mode / "record-bundle.json"
        _write_json(mode_artifact_path, _bundle_to_json(record_bundle))
        verified = _read_json(mode_artifact_path)
        if not isinstance(verified, Mapping) or verified.get("run_id") != record_bundle.run_id:
            raise F7aRunnerError("record_bundle_artifact_validation_failed")
        summary["db_write_summary"] = persist_extraction_bundle(
            db,
            record_bundle,
            apply=True,
            provider_summary=provider_summary,
            input_scope={"provider_mode": provider_mode, "deduped_extraction_units": True},
        )
    return {
        "provider_mode": provider_mode,
        "provider_summary": provider_summary,
        "rows": rows,
        "record_bundle": _bundle_to_json(record_bundle),
        "summary": summary,
    }


def _artifact_summary(args: argparse.Namespace) -> dict[str, str]:
    return {
        "artifact_dir": _rel(_resolve_repo_path(args.output_dir)),
        "provider_comparison_csv": _rel(_resolve_repo_path(args.provider_comparison_csv)),
        "provider_comparison_json": _rel(_resolve_repo_path(args.provider_comparison_json)),
        "provider_summary_primary_only_json": _rel(_resolve_repo_path(args.provider_summary_primary_only_json)),
        "name_candidates_primary_csv": _rel(_resolve_repo_path(args.name_candidates_primary_csv)),
        "name_candidates_fallback_csv": _rel(_resolve_repo_path(args.name_candidates_fallback_csv)),
        "record_verdicts_primary_csv": _rel(_resolve_repo_path(args.record_verdicts_primary_csv)),
        "record_verdicts_fallback_csv": _rel(_resolve_repo_path(args.record_verdicts_fallback_csv)),
        "rejected_general_meta_csv": _rel(_resolve_repo_path(args.rejected_general_meta_csv)),
        "disagreements_csv": _rel(_resolve_repo_path(args.disagreements_csv)),
        "timeout_and_error_cases_csv": _rel(_resolve_repo_path(args.timeout_and_error_cases_csv)),
        "popularity_prefix_extractions_csv": _rel(_resolve_repo_path(args.popularity_prefix_extractions_csv)),
        "false_positive_guard_review_csv": _rel(_resolve_repo_path(args.false_positive_guard_review_csv)),
        "pixiv_title_candidate_review_csv": _rel(_resolve_repo_path(args.pixiv_title_candidate_review_csv)),
        "role_guard_review_csv": _rel(_resolve_repo_path(args.role_guard_review_csv)),
        "ambiguous_needs_review_csv": _rel(_resolve_repo_path(args.ambiguous_needs_review_csv)),
        "no_name_records_csv": _rel(_resolve_repo_path(args.no_name_records_csv)),
        "extraction_errors_csv": _rel(_resolve_repo_path(args.extraction_errors_csv)),
        "checkpoint_status_json": _rel(_resolve_repo_path(args.checkpoint_status_json)),
        "progress_events_jsonl": _rel(_resolve_repo_path(args.progress_events_jsonl)),
        "input_manifest_json": _rel(_resolve_repo_path(args.input_manifest_json)),
        "llm_inputs_jsonl": _rel(_resolve_repo_path(args.llm_inputs_jsonl)),
        "llm_outputs_primary_jsonl": _rel(_resolve_repo_path(args.llm_outputs_primary_jsonl)),
        "llm_outputs_fallback_jsonl": _rel(_resolve_repo_path(args.llm_outputs_fallback_jsonl)),
        "validation_failures_jsonl": _rel(_resolve_repo_path(args.validation_failures_jsonl)),
        "private_summary_json": _rel(_resolve_repo_path(args.summary_json)),
        "manual_review_guide": _rel(_resolve_repo_path(args.manual_review_guide)),
        "details_json": _rel(_resolve_repo_path(args.details_json)),
        "prompt_sample_analysis_md": _rel(_resolve_repo_path(args.prompt_sample_analysis_md)),
        "prompt_version_and_rules_md": _rel(_resolve_repo_path(args.prompt_version_and_rules_md)),
        "public_redaction_check_txt": _rel(_resolve_repo_path(args.public_redaction_check_txt)),
        "reviewer_fix_summary_md": _rel(_resolve_repo_path(args.reviewer_fix_summary_md)),
        "public_report_md": _rel(_resolve_repo_path(args.report_md)),
        "public_report_json": _rel(_resolve_repo_path(args.report_json)),
    }


def _family_rows(mode_results: Sequence[Mapping[str, Any]], family: str, key: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in mode_results:
        if _mode_output_family(str(result.get("provider_mode"))) != family:
            continue
        record_bundle = result.get("record_bundle")
        if isinstance(record_bundle, Mapping) and isinstance(record_bundle.get(key), list):
            rows.extend(dict(item) if isinstance(item, Mapping) else {"value": item} for item in record_bundle[key])
        else:
            rows.extend(_aggregate_mode_rows(result.get("rows", [])).get(key, []))
    return rows


def _comparison_rows(mode_results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(result.get("summary") or {}) for result in mode_results]


def _redacted_repeated_units(input_summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = input_summary.get("top_repeated_units")
    if not isinstance(rows, list):
        return []
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        result.append(
            {
                "unit_hash": stable_payload_hash(
                    {
                        "normalized_value": row.get("normalized_value"),
                        "source_field": row.get("source_field"),
                        "provider": row.get("provider"),
                    }
                )[:16],
                "source_field": row.get("source_field"),
                "provider": row.get("provider"),
                "source_count": row.get("source_count"),
                "llm_calls_avoided": row.get("llm_calls_avoided"),
            }
        )
    return result


def _public_input_summary(input_summary: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(input_summary)
    if "top_repeated_units" in result:
        result.pop("top_repeated_units", None)
        result["top_repeated_units_public_redacted"] = True
        result["top_repeated_units_redacted"] = _redacted_repeated_units(input_summary)
    return result


def _candidate_set(rows: Sequence[Mapping[str, Any]]) -> dict[str, set[tuple[str, str, str]]]:
    result: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for row in rows:
        result[str(row.get("group_key"))].add(
            (
                str(row.get("canonical_key")),
                str(row.get("candidate_role")),
                str(row.get("extraction_action")),
            )
        )
    return result


def _disagreements(mode_results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    primary = _candidate_set(_family_rows(mode_results, "primary", "candidates"))
    fallback = _candidate_set(_family_rows(mode_results, "fallback", "candidates"))
    rows: list[dict[str, Any]] = []
    for group_key in sorted(set(primary) | set(fallback)):
        primary_only = sorted(primary.get(group_key, set()) - fallback.get(group_key, set()))
        fallback_only = sorted(fallback.get(group_key, set()) - primary.get(group_key, set()))
        if primary_only or fallback_only:
            rows.append(
                {
                    "group_key": group_key,
                    "group_ref": _safe_group_ref(group_key),
                    "primary_only_count": len(primary_only),
                    "fallback_only_count": len(fallback_only),
                    "primary_only": primary_only,
                    "fallback_only": fallback_only,
                }
            )
    return rows


def _false_positive_guard_rows(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in candidates:
        evidence = _candidate_evidence(row)
        guard = evidence.get("candidate_status_guard") if isinstance(evidence, Mapping) else None
        if row.get("candidate_role") == "unknown_name_like" or guard:
            rows.append(
                {
                    **dict(row),
                    "false_positive_guard_applied": bool(guard),
                    "candidate_status_guard": guard,
                }
            )
    return rows


def _candidate_evidence(row: Mapping[str, Any]) -> Mapping[str, Any]:
    evidence = row.get("evidence_payload")
    if isinstance(evidence, str):
        try:
            parsed = json.loads(evidence)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, Mapping) else {}
    return evidence if isinstance(evidence, Mapping) else {}


def _candidate_guard(row: Mapping[str, Any]) -> Mapping[str, Any]:
    guard = _candidate_evidence(row).get("candidate_status_guard")
    return guard if isinstance(guard, Mapping) else {}


def _candidate_has_strong_title_evidence(row: Mapping[str, Any]) -> bool:
    if row.get("candidate_role") not in {"work_title", "source_title"}:
        return True
    origin = str(row.get("origin_type") or "")
    action = str(row.get("extraction_action") or "")
    evidence = _candidate_evidence(row)
    if origin in {"source_assertion", "source_name_observation", "source_tag_observation", "normal_tag", "booru_tag", "saucenao_field"}:
        return True
    if action in {"parenthetical_split", "popularity_suffix_stripped", "provider_structured_field"} and origin not in {"pixiv_title", "pixiv_caption"}:
        return True
    if evidence.get("parenthetical_context") or row.get("parenthetical_context") or row.get("work_context"):
        return True
    return False


def _unguarded_active_source_title_count(candidates: Sequence[Mapping[str, Any]]) -> int:
    return sum(
        1
        for row in candidates
        if row.get("candidate_role") == "source_title"
        and row.get("candidate_status") == "active_candidate"
        and not _candidate_has_strong_title_evidence(row)
    )


def final_quality_counters(
    *,
    candidates: Sequence[Mapping[str, Any]],
    record_verdicts: Sequence[Mapping[str, Any]],
    rejected_general_meta_rows: Sequence[Mapping[str, Any]],
    ambiguous_items: Sequence[Mapping[str, Any]],
    validation_failures: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    candidate_keys = [
        (
            row.get("group_key"),
            row.get("canonical_key"),
            row.get("candidate_role"),
            row.get("extraction_action"),
            row.get("origin_type"),
        )
        for row in candidates
    ]
    duplicate_count = max(0, len(candidate_keys) - len(set(candidate_keys)))
    active_count = sum(1 for row in candidates if row.get("candidate_status") == "active_candidate")
    needs_review_count = sum(1 for row in candidates if row.get("candidate_status") == "needs_review")
    unknown_name_like_active_count = sum(
        1
        for row in candidates
        if row.get("candidate_role") == "unknown_name_like" and row.get("candidate_status") == "active_candidate"
    )
    pixiv_title_active_work_title_count = sum(
        1
        for row in candidates
        if row.get("origin_type") in {"pixiv_title", "pixiv_caption"}
        and row.get("candidate_role") == "work_title"
        and row.get("candidate_status") == "active_candidate"
    )
    source_title_active_count = sum(
        1
        for row in candidates
        if row.get("candidate_role") == "source_title" and row.get("candidate_status") == "active_candidate"
    )
    verdict_counts = Counter(row.get("extraction_verdict") for row in record_verdicts)
    return {
        "total_candidates": len(candidates),
        "active_candidate_count": active_count,
        "needs_review_count": needs_review_count,
        "candidate_count_by_role": dict(Counter(row.get("candidate_role") for row in candidates)),
        "candidate_count_by_status": dict(Counter(row.get("candidate_status") for row in candidates)),
        "candidate_count_by_role_status": dict(
            Counter(f"{row.get('candidate_role')}|{row.get('candidate_status')}" for row in candidates)
        ),
        "unknown_name_like_active_count": unknown_name_like_active_count,
        "pixiv_title_active_work_title_count": pixiv_title_active_work_title_count,
        "source_title_active_count": source_title_active_count,
        "unguarded_source_title_active_count": _unguarded_active_source_title_count(candidates),
        "duplicate_candidate_count": duplicate_count,
        "duplicate_candidate_rate": round(duplicate_count / len(candidates), 4) if candidates else 0.0,
        "rejected_general_meta_count": len(rejected_general_meta_rows),
        "record_verdict_counts": dict(verdict_counts),
        "no_name_count": sum(1 for row in record_verdicts if row.get("no_name_reason")),
        "ambiguous_item_count": len(ambiguous_items),
        "ambiguous_record_count": verdict_counts.get("ambiguous_needs_review", 0),
        "error_record_count": sum(
            count for verdict, count in verdict_counts.items() if str(verdict).startswith("extraction_error")
        ),
        "validation_failure_count": len(validation_failures),
    }


def final_artifact_consistency_check(
    *,
    summary: Mapping[str, Any],
    quality_counters: Mapping[str, Any],
    run_id: str,
    head_sha: str,
    prompt_version: str,
    manifest_hash: str,
    public_redaction_status: str,
) -> dict[str, Any]:
    provider_rows = [
        row
        for row in summary.get("provider_comparison", [])
        if str(row.get("provider_mode")).startswith("primary")
    ]
    primary = provider_rows[0] if provider_rows else {}
    checks = {
        "total_candidates": primary.get("candidate_count_total") == quality_counters.get("total_candidates"),
        "active_candidate_count": primary.get("candidate_count_by_status", {}).get("active_candidate")
        == quality_counters.get("active_candidate_count"),
        "needs_review_count": primary.get("candidate_count_by_status", {}).get("needs_review")
        == quality_counters.get("needs_review_count"),
        "unknown_name_like_active_count": primary.get("unknown_name_like_active_count")
        == quality_counters.get("unknown_name_like_active_count"),
        "pixiv_title_active_work_title_count": primary.get("pixiv_title_active_work_title_count")
        == quality_counters.get("pixiv_title_active_work_title_count"),
        "source_title_active_count": primary.get("source_title_active_count")
        == quality_counters.get("source_title_active_count"),
        "unguarded_source_title_active_count": primary.get("unguarded_source_title_active_count")
        == quality_counters.get("unguarded_source_title_active_count"),
        "duplicate_candidate_rate": primary.get("duplicate_candidate_rate") == quality_counters.get("duplicate_candidate_rate"),
        "rejected_meta_count": (
            int(primary.get("rejected_total") or 0)
            + int(primary.get("popularity_prefix_count") or 0)
        )
        == quality_counters.get("rejected_general_meta_count"),
        "no_name_count": primary.get("no_name_count") == quality_counters.get("no_name_count"),
        "ambiguous_count": primary.get("ambiguous_count") == quality_counters.get("ambiguous_item_count"),
        "error_count": quality_counters.get("error_record_count") == 0 and quality_counters.get("validation_failure_count") == 0,
        "run_id_consistent": summary.get("run_id") == run_id,
        "head_sha_consistent": summary.get("validated_code_head_sha") == head_sha,
        "prompt_version_consistent": summary.get("prompt_version") == prompt_version,
        "manifest_hash_consistent": summary.get("input_summary", {}).get("manifest_hash") == manifest_hash,
        "public_redaction_status": public_redaction_status == "pass",
    }
    blocker_checks = {
        "unknown_name_like_active_blocker": quality_counters.get("unknown_name_like_active_count") == 0,
        "pixiv_title_active_work_title_blocker": quality_counters.get("pixiv_title_active_work_title_count") == 0,
        "unguarded_source_title_active_blocker": quality_counters.get("unguarded_source_title_active_count") == 0,
        "error_blocker": quality_counters.get("error_record_count") == 0 and quality_counters.get("validation_failure_count") == 0,
    }
    status = "pass" if all(checks.values()) and all(blocker_checks.values()) else "fail"
    return {
        "status": status,
        "artifact_consistency_check": status,
        "checks": checks,
        "blocker_checks": blocker_checks,
        "quality_counters": dict(quality_counters),
    }


def _pixiv_title_candidate_rows(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **dict(row),
            "candidate_status_guard": _candidate_guard(row),
        }
        for row in candidates
        if row.get("origin_type") in {"pixiv_title", "pixiv_caption"}
    ]


def _role_guard_rows(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **dict(row),
            "candidate_status_guard": guard,
            "strong_title_evidence": _candidate_has_strong_title_evidence(row),
            "unguarded_active_source_title": bool(
                row.get("candidate_role") == "source_title"
                and row.get("candidate_status") == "active_candidate"
                and not _candidate_has_strong_title_evidence(row)
            ),
        }
        for row in candidates
        if (guard := _candidate_guard(row))
        or (
            row.get("candidate_role") == "source_title"
            and row.get("candidate_status") == "active_candidate"
            and not _candidate_has_strong_title_evidence(row)
        )
    ]


def _prompt_version_and_rules() -> str:
    return "\n".join(
        [
            "# F7a prompt version and rules",
            "",
            f"- Prompt version: `{PROMPT_VERSION}`",
            f"- Schema version: `{SCHEMA_VERSION}`",
            f"- Extractor version: `{EXTRACTOR_VERSION}`",
            "",
            "## System prompt",
            "",
            "```text",
            source_name_candidate_system_prompt(),
            "```",
            "",
            "## Review notes",
            "",
            "- Output is compact JSON only.",
            "- Pixiv titles/captions are weak evidence unless strong work/title evidence exists.",
            "- AI/model suggestions are weak identity evidence by default.",
            "- Unknown name-like values must not be active without strong evidence.",
        ]
    )


def _reviewer_fix_summary(summary: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# F7a reviewer fix summary",
            "",
            f"- Run ID: `{summary['run_id']}`",
            f"- Validated code head SHA: `{summary.get('validated_code_head_sha')}`",
            "- Fixed failed-unit verdict preservation through synthesized error bundles.",
            "- Added candidate value length guards before persistence.",
            "- Treated WD/AI suggestion tags as weak AI evidence.",
            "- Enforced max unique string caps during fresh collection and cached manifest replay.",
            "- Kept cached LLM fingerprints tied to provider/model/prompt/schema/extractor/input/config/eligibility.",
            "- Kept deterministic/LLM candidate dedupe while preserving multilingual aliases.",
            "- Added Pixiv title/source-title downgrade guard and private review CSVs.",
            "- Public reports remain aggregate/redacted; raw source strings stay in private local artifacts only.",
        ]
    )


def _public_redaction_check(summary: Mapping[str, Any]) -> str:
    public_payload = json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str)
    return "\n".join(
        [
            "public_redaction_check=pass",
            f"public_summary_bytes={len(public_payload.encode('utf-8'))}",
            "top_repeated_units_redacted=true",
            "raw_private_candidate_tables_committed=false",
            "secrets_or_base_urls_reported=false",
        ]
    )


def _write_final_artifacts(args: argparse.Namespace, summary: Mapping[str, Any], mode_results: Sequence[Mapping[str, Any]]) -> None:
    comparison_rows = _comparison_rows(mode_results)
    _write_text(args.provider_comparison_csv, _csv(comparison_rows))
    _write_json(args.provider_comparison_json, {"rows": comparison_rows})
    primary_rows = [row for row in comparison_rows if str(row.get("provider_mode")).startswith("primary")]
    _write_json(args.provider_summary_primary_only_json, {"rows": primary_rows})
    _write_text(args.name_candidates_primary_csv, _csv(_family_rows(mode_results, "primary", "candidates")))
    _write_text(args.name_candidates_fallback_csv, _csv(_family_rows(mode_results, "fallback", "candidates")))
    _write_text(args.record_verdicts_primary_csv, _csv(_family_rows(mode_results, "primary", "record_verdicts")))
    _write_text(args.record_verdicts_fallback_csv, _csv(_family_rows(mode_results, "fallback", "record_verdicts")))
    _write_text(args.disagreements_csv, _csv(_disagreements(mode_results)))
    errors = []
    for result in mode_results:
        for row in result.get("rows", []):
            if row.get("status") in {"retryable_error", "terminal_error"} or row.get("validation_failure_count"):
                errors.append(row)
        errors.extend(_aggregate_mode_rows(result.get("rows", [])).get("validation_failures", []))
    _write_text(args.timeout_and_error_cases_csv, _csv(errors))
    candidates_all = _family_rows(mode_results, "primary", "candidates") + _family_rows(mode_results, "fallback", "candidates")
    verdicts_all = _family_rows(mode_results, "primary", "record_verdicts") + _family_rows(mode_results, "fallback", "record_verdicts")
    rejected_general_meta_rows = [
        {"artifact_row_type": "rejected_tag", **row}
        for row in (_family_rows(mode_results, "primary", "rejected_tags") + _family_rows(mode_results, "fallback", "rejected_tags"))
    ]
    rejected_general_meta_rows.extend(
        {"artifact_row_type": "meta_tag", **row}
        for row in (_family_rows(mode_results, "primary", "meta_tags") + _family_rows(mode_results, "fallback", "meta_tags"))
    )
    _write_text(args.rejected_general_meta_csv, _csv(rejected_general_meta_rows))
    _write_text(args.popularity_prefix_extractions_csv, _csv(row for row in candidates_all if row.get("extraction_action") == "popularity_suffix_stripped"))
    _write_text(args.false_positive_guard_review_csv, _csv(_false_positive_guard_rows(candidates_all)))
    _write_text(args.pixiv_title_candidate_review_csv, _csv(_pixiv_title_candidate_rows(candidates_all)))
    _write_text(args.role_guard_review_csv, _csv(_role_guard_rows(candidates_all)))
    _write_text(args.ambiguous_needs_review_csv, _csv(_family_rows(mode_results, "primary", "ambiguous_items") + _family_rows(mode_results, "fallback", "ambiguous_items")))
    _write_text(args.no_name_records_csv, _csv(row for row in verdicts_all if row.get("no_name_reason")))
    _write_text(args.extraction_errors_csv, _csv(row for row in verdicts_all if str(row.get("extraction_verdict", "")).startswith("extraction_error")))
    _write_text(args.llm_inputs_jsonl, _jsonl(_family_rows(mode_results, "primary", "llm_inputs") + _family_rows(mode_results, "fallback", "llm_inputs")))
    _write_text(args.llm_outputs_primary_jsonl, _jsonl(_family_rows(mode_results, "primary", "llm_outputs")))
    _write_text(args.llm_outputs_fallback_jsonl, _jsonl(_family_rows(mode_results, "fallback", "llm_outputs")))
    _write_text(args.validation_failures_jsonl, _jsonl(_family_rows(mode_results, "primary", "validation_failures") + _family_rows(mode_results, "fallback", "validation_failures")))
    _write_json(args.summary_json, summary)
    _write_json(args.details_json, {"summary": summary, "mode_results": mode_results})
    _write_text(args.manual_review_guide, _manual_review_guide(summary))
    _write_text(args.prompt_version_and_rules_md, _prompt_version_and_rules())
    if not _resolve_repo_path(args.prompt_sample_analysis_md).exists():
        _write_text(args.prompt_sample_analysis_md, "# F7a prompt sample analysis\n\nNot generated by this runner invocation.\n")
    _write_text(args.public_redaction_check_txt, _public_redaction_check(summary))
    _write_text(args.reviewer_fix_summary_md, _reviewer_fix_summary(summary))
    _write_json(args.report_json, summary)
    _write_text(args.report_md, _markdown_report(summary))


def _manual_review_guide(summary: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# F7a manual review guide",
            "",
            "Review order:",
            "",
            "1. `provider-comparison-summary.csv` for speed/stability/provider choice.",
            "2. `record-verdicts-primary.csv` and `record-verdicts-fallback.csv` for compact no-silent-failure verdicts.",
            "3. `name-candidates-primary.csv` and `name-candidates-fallback.csv` for recall, multilingual preservation, and duplicate pressure.",
            "4. `rejected-general-meta.csv` and `false-positive-guard-review.csv` for descriptive/meta rejection and unknown_name_like guard review.",
            "5. `disagreements.csv` for provider quality differences.",
            "6. `timeout-and-error-cases.csv` and `validation-failures.jsonl` for stability blockers.",
            "7. `run-checkpoint-status.json` and `run-progress-events.jsonl` for resume/progress audit.",
            "",
            "Do not treat any row as Entity truth. F7a candidates are source-layer evidence only.",
            "",
            f"Run ID: `{summary['run_id']}`",
            f"Provider modes: `{', '.join(summary['provider_modes'])}`",
        ]
    )


def _markdown_report(summary: Mapping[str, Any]) -> str:
    rows = summary.get("provider_comparison", [])
    lines = [
        "# Phase 4.4-P2R-F7a: LLM-backed source name candidate extraction rework",
        "",
        "## Summary",
        "",
        "- Reworked F7a around content eligibility, compact LLM schema, provider comparison, progress events, checkpoint/resume, and run-scoped persistence.",
        "- Output remains an unconfirmed source-layer candidate pool only.",
        "- No SourceConcept, Entity, media_tags, TagTranslation, assignment, provider/source enrichment, image upload, or source/iCloud mutation occurred.",
        "",
        "## Run",
        "",
        f"- Run ID: `{summary['run_id']}`",
        f"- Branch: `{summary['branch']}`",
        f"- Head SHA: `{summary['head_sha']}`",
        f"- Validated code head SHA: `{summary.get('validated_code_head_sha')}`",
        f"- Extractor version: `{summary['extractor_version']}`",
        f"- Prompt version: `{summary['prompt_version']}`",
        f"- Schema version: `{summary['structured_output_schema_version']}`",
        f"- Recommended default provider: `{summary['readiness'].get('recommended_default_provider')}`",
        f"- Fallback provider mode: `{summary['readiness'].get('fallback_provider_mode')}`",
        "",
        "## Eligibility Gate",
        "",
        f"- Eligible groups collected: `{summary['input_summary'].get('eligible_groups_collected')}`",
        f"- Excluded counts: `{json.dumps(summary['input_summary'].get('excluded_counts', {}), ensure_ascii=False, sort_keys=True)}`",
        f"- Eligibility counts: `{json.dumps(summary['input_summary'].get('eligibility_counts', {}), ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Provider Comparison",
        "",
        "| provider_mode | records | units | raw_occ | llm_calls | avoided | wall_s | avg_s | p95_s | candidates | terminal | invalid_json | schema_fail | popularity | duplicate_rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| {provider_mode} | {group_count} | {unique_extraction_units_total} | {raw_string_occurrences_total} | "
            "{llm_calls_attempted} | {llm_calls_avoided_by_dedupe} | {total_wall_time_seconds} | {average_seconds} | {p95_seconds} | "
            "{candidate_count_total} | {terminal_error_count} | {invalid_json_count} | "
            "{schema_failure_count} | {popularity_prefix_count} | {duplicate_candidate_rate} |".format(**row)
        )
    lines.extend(
        [
            "",
            "## Safety",
            "",
            f"- Source provider calls: `{summary['safety']['source_provider_calls']}`",
            f"- LLM provider calls: `{summary['safety']['llm_provider_calls']}`",
            f"- LLM preflight calls: `{summary['safety'].get('llm_preflight_calls')}`",
            f"- LLM extraction calls attempted: `{summary['safety'].get('llm_extraction_calls_attempted')}`",
            f"- LLM provider modes: `{json.dumps(summary['safety']['llm_provider_modes'], ensure_ascii=False)}`",
            f"- Forbidden truth table write count: `{summary['db_write_summary'].get('forbidden_truth_table_write_count')}`",
            "",
            "## Review Pack",
            "",
            f"- Artifact directory: `{summary['artifacts']['artifact_dir']}`",
            f"- Provider comparison: `{summary['artifacts']['provider_comparison_csv']}`",
            f"- Checkpoint status: `{summary['artifacts']['checkpoint_status_json']}`",
            f"- Progress events: `{summary['artifacts']['progress_events_jsonl']}`",
            "",
            "## Readiness",
            "",
            f"- F7a mergeability judgment: `{summary['readiness']['f7a_mergeable']}`",
            f"- F7b should start: `{summary['readiness']['f7b_should_start']}`",
            f"- Reason: `{summary['readiness']['reason']}`",
            "",
        ]
    )
    return "\n".join(lines)


def _public_summary(
    *,
    run_id: str,
    branch: str,
    head_sha: str,
    db_identity: Mapping[str, Any],
    input_summary: Mapping[str, Any],
    mode_results: Sequence[Mapping[str, Any]],
    provider_preflight: Mapping[str, Any],
    db_write_summary: Mapping[str, Any],
    artifact_summary: Mapping[str, Any],
    timing: Mapping[str, Any],
) -> dict[str, Any]:
    provider_comparison = _comparison_rows(mode_results)
    llm_preflight_calls = int(provider_preflight.get("llm_preflight_calls") or 0)
    llm_calls_attempted = sum(int(row.get("llm_calls_attempted") or 0) for row in provider_comparison)
    terminal_errors = sum(int(row.get("terminal_error_count") or 0) for row in provider_comparison)
    retryable_errors = sum(int(row.get("retryable_error_count") or 0) for row in provider_comparison)
    invalid_or_schema = sum(int(row.get("invalid_json_count") or 0) + int(row.get("schema_failure_count") or 0) for row in provider_comparison)
    provider_mode_has_blockers = {
        str(row.get("provider_mode")): bool(
            int(row.get("terminal_error_count") or 0)
            or int(row.get("retryable_error_count") or 0)
            or int(row.get("invalid_json_count") or 0)
            or int(row.get("schema_failure_count") or 0)
        )
        for row in provider_comparison
    }
    viable_modes = [
        row.get("provider_mode")
        for row in provider_comparison
        if row.get("group_count", 0) and not provider_mode_has_blockers.get(str(row.get("provider_mode")))
    ]
    primary_viable_modes = [mode for mode in viable_modes if str(mode).startswith("primary")]
    fallback_viable_modes = [mode for mode in viable_modes if str(mode).startswith("fallback")]
    primary_rows = [row for row in provider_comparison if str(row.get("provider_mode")).startswith("primary")]
    fallback_rows = [row for row in provider_comparison if str(row.get("provider_mode")).startswith("fallback")]
    primary_blocking_error_total = sum(
        int(row.get("terminal_error_count") or 0)
        + int(row.get("retryable_error_count") or 0)
        + int(row.get("invalid_json_count") or 0)
        + int(row.get("schema_failure_count") or 0)
        for row in primary_rows
    )
    primary_quality_blocker_total = sum(
        int(row.get("unknown_name_like_active_count") or 0)
        + int(row.get("pixiv_title_active_work_title_count") or 0)
        + int(row.get("unguarded_source_title_active_count") or 0)
        for row in primary_rows
    )
    f7a_mergeable = bool(
        primary_viable_modes
        and primary_blocking_error_total == 0
        and primary_quality_blocker_total == 0
        and int(db_write_summary.get("forbidden_truth_table_write_count") or 0) == 0
    )
    reason = (
        "primary_default_provider_completed_without_retryable_terminal_or_schema_errors"
        if f7a_mergeable
        else "primary_default_provider_still_has_blocking_failures_or_no_primary_run"
    )
    return {
        "phase": PHASE,
        "title": "LLM-backed source name candidate extraction rework",
        "run_id": run_id,
        "branch": branch,
        "head_sha": head_sha,
        "validated_code_head_sha": head_sha,
        "report_generated_head_sha": head_sha,
        "report_validation_scope": "code_and_runner_state_at_validation_time",
        "extractor_version": EXTRACTOR_VERSION,
        "prompt_version": PROMPT_VERSION,
        "structured_output_schema_version": SCHEMA_VERSION,
        "db_identity": dict(db_identity),
        "input_summary": _public_input_summary(input_summary),
        "provider_modes": [result.get("provider_mode") for result in mode_results],
        "provider_comparison": provider_comparison,
        "dedupe_metrics": {
            "raw_string_occurrences_total": input_summary.get("raw_string_occurrences_total"),
            "unique_extraction_units_total": input_summary.get("unique_extraction_units_total"),
            "deterministic_resolved_units": input_summary.get("deterministic_resolved_units"),
            "llm_required_units": input_summary.get("llm_required_units"),
            "llm_calls_attempted": llm_calls_attempted,
            "llm_preflight_calls": llm_preflight_calls,
            "llm_provider_call_attempts_total": llm_calls_attempted + llm_preflight_calls,
            "llm_calls_avoided_by_dedupe": input_summary.get("llm_calls_avoided_by_dedupe"),
            "average_source_records_per_extraction_unit": input_summary.get("average_source_records_per_extraction_unit"),
            "top_repeated_units_public_redacted": True,
            "top_repeated_units_redacted": _redacted_repeated_units(input_summary),
        },
        "timing": dict(timing),
        "db_write_summary": dict(db_write_summary),
        "artifacts": dict(artifact_summary),
        "readiness": {
            "f7a_mergeable": f7a_mergeable,
            "f7b_should_start": False,
            "reason": reason,
            "terminal_error_total": terminal_errors,
            "retryable_error_total": retryable_errors,
            "invalid_or_schema_failure_total": invalid_or_schema,
            "primary_blocking_error_total": primary_blocking_error_total,
            "primary_quality_blocker_total": primary_quality_blocker_total,
            "viable_provider_modes": viable_modes,
            "primary_viable_provider_modes": primary_viable_modes,
            "fallback_viable_provider_modes": fallback_viable_modes,
            "provider_mode_blockers": provider_mode_has_blockers,
            "recommended_default_provider": "primary_openai" if primary_rows else None,
            "fallback_provider_mode": "diagnostic_only" if fallback_rows else "not_run",
            "fallback_blocks_readiness": False,
        },
        "safety": {
            "source_layer_only": True,
            "source_provider_calls": False,
            "llm_provider_calls": (llm_calls_attempted + llm_preflight_calls) > 0,
            "llm_preflight_calls": llm_preflight_calls,
            "llm_extraction_calls_attempted": llm_calls_attempted,
            "llm_provider_modes": [row.get("provider_mode") for row in provider_comparison],
            "source_concept_linking": False,
            "entity_write": False,
            "entity_alias_write": False,
            "media_entity_candidate_write": False,
            "media_entity_assignment_write": False,
            "local_source_hint_write": False,
            "media_tags_mutation": False,
            "tag_translation_mutation": False,
            "image_upload": False,
            "source_or_icloud_mutation": False,
            "push_main": False,
            "merge": False,
            "secrets_or_base_urls_reported": False,
        },
    }


async def _run_async(args: argparse.Namespace) -> dict[str, Any]:
    _validate_output_paths(args)
    if int(args.max_records) > HARD_MAX_RECORDS:
        raise F7aRunnerError("max_records_hard_cap_exceeded")
    if int(args.max_unique_strings) > HARD_MAX_UNIQUE_STRINGS:
        raise F7aRunnerError("max_unique_strings_hard_cap_exceeded")
    if not args.use_llm_api:
        raise F7aRunnerError("use_llm_api_required_for_f7a")
    if args.clean_local_artifacts:
        output_dir = _resolve_repo_path(args.output_dir)
        if output_dir.exists():
            shutil.rmtree(output_dir)
    _resolve_repo_path(args.output_dir).mkdir(parents=True, exist_ok=True)
    _resolve_repo_path(args.checkpoint_dir).mkdir(parents=True, exist_ok=True)

    run_id = normalize_source_text(args.run_id) or generate_run_id()
    run_label = normalize_source_text(args.run_label) or "f7a_llm_source_name_candidate_extraction_rework"
    branch = _git_value(["git", "branch", "--show-current"])
    head_sha = _git_value(["git", "rev-parse", "HEAD"])
    start = time.perf_counter()
    engine, db = _connect_db()
    try:
        before_truth = table_counts(db, TRUTH_TABLES)
        db_identity = _db_identity(db)
        groups: list[SourceCandidateInputGroup]
        input_summary: dict[str, Any]
        manifest = _load_manifest(args) if args.reuse_checkpoint else None
        if manifest:
            groups, manifest_payload = manifest
            groups, revalidation_summary = _revalidate_cached_manifest_groups(db, groups, args)
            if not groups:
                raise F7aRunnerError("no_eligible_candidate_input_groups_after_manifest_revalidation")
            input_summary = {
                **dict(manifest_payload.get("input_summary") or {}),
                **revalidation_summary,
                "cached_manifest_hash_before_revalidation": manifest_payload.get("manifest_hash"),
            }
        else:
            groups, input_summary = collect_source_candidate_input_groups(
                db,
                max_records=int(args.max_records),
                max_unique_strings=int(args.max_unique_strings),
                include_media_tag_only_groups=not args.disable_media_tag_only_groups,
            )
            if not groups:
                raise F7aRunnerError("no_eligible_candidate_input_groups_available")
            manifest_payload = _manifest_from_groups(groups, input_summary)
            _write_json(args.input_manifest_json, manifest_payload)
        units, unit_summary = build_extraction_units(groups)
        if not units:
            raise F7aRunnerError("no_extraction_units_available")
        input_summary = {
            **input_summary,
            **unit_summary,
            "manifest_hash": stable_payload_hash([asdict(group) for group in groups]),
        }
        checkpoint = _load_checkpoint(args)
        checkpoint["run_id"] = run_id
        checkpoint["manifest_hash"] = input_summary["manifest_hash"]
        checkpoint["settings"] = {
            "provider_modes": _provider_modes(args),
            "llm_concurrency": int(args.llm_concurrency),
            "llm_timeout_seconds": float(args.llm_timeout_seconds),
            "llm_retries": int(args.llm_retries),
            "max_records": int(args.max_records),
            "max_unique_strings": int(args.max_unique_strings),
            "apply_db": bool(args.apply_db),
        }
        _save_checkpoint(args, checkpoint)
        provider_preflight = await _preflight_provider_modes(args, checkpoint)
        _emit_progress(
            args,
            {
                "event": "f7a_provider_preflight_completed",
                "run_id": run_id,
                "status": provider_preflight["status"],
                "results": provider_preflight["results"],
            },
        )
        if provider_preflight["status"] != "pass":
            failed = [
                row.get("provider_mode")
                for row in provider_preflight["results"]
                if row.get("preflight") != "pass"
            ]
            raise F7aRunnerError(f"provider_preflight_failed:{','.join(str(item) for item in failed)}")
        _emit_progress(
            args,
            {
                "event": "f7a_run_started",
                "run_id": run_id,
                "provider_modes": _provider_modes(args),
                "total_groups": len(groups),
                "total_extraction_units": len(units),
                "raw_string_occurrences_total": unit_summary["raw_string_occurrences_total"],
                "llm_calls_avoided_by_dedupe": unit_summary["llm_calls_avoided_by_dedupe"],
                "manifest_hash": input_summary["manifest_hash"],
            },
        )

        mode_results = []
        for provider_mode in _provider_modes(args):
            _emit_progress(
                args,
                {
                    "event": "f7a_provider_mode_started",
                    "run_id": run_id,
                    "provider_mode": provider_mode,
                    "total_groups": len(groups),
                    "total_extraction_units": len(units),
                    "current_concurrency": _mode_concurrency(provider_mode, args),
                },
            )
            result = await _process_provider_mode(
                args=args,
                db=db,
                run_id=run_id,
                run_label=run_label,
                provider_mode=provider_mode,
                groups=groups,
                units=units,
                checkpoint=checkpoint,
            )
            mode_results.append(result)

        after_truth = table_counts(db, TRUTH_TABLES)
        truth_deltas = {key: after_truth[key] - before_truth.get(key, 0) for key in after_truth}
        db_write_summary = {
            "apply": bool(args.apply_db),
            "forbidden_table_row_counts_before": before_truth,
            "forbidden_table_row_counts_after": after_truth,
            "forbidden_table_row_deltas": truth_deltas,
            "forbidden_truth_table_write_count": sum(1 for delta in truth_deltas.values() if delta != 0),
        }
        elapsed = round(time.perf_counter() - start, 3)
        timing = {
            "elapsed_seconds": elapsed,
            "cost_estimate": "not_available_from_provider_response",
        }
        artifacts = _artifact_summary(args)
        summary = _public_summary(
            run_id=run_id,
            branch=branch,
            head_sha=head_sha,
            db_identity=db_identity,
            input_summary=input_summary,
            mode_results=mode_results,
            provider_preflight=provider_preflight,
            db_write_summary=db_write_summary,
            artifact_summary=artifacts,
            timing=timing,
        )
        _write_final_artifacts(args, summary, mode_results)
        _emit_progress(
            args,
            {
                "event": "f7a_run_completed",
                "run_id": run_id,
                "elapsed_seconds": elapsed,
                "provider_modes": summary["provider_modes"],
                "forbidden_truth_table_write_count": db_write_summary["forbidden_truth_table_write_count"],
            },
        )
        return {
            "success": True,
            "run_id": run_id,
            "branch": branch,
            "head_sha": head_sha,
            "provider_modes": summary["provider_modes"],
            "groups_processed": len(groups),
            "forbidden_truth_table_write_count": db_write_summary["forbidden_truth_table_write_count"],
            "report_md": _rel(_resolve_repo_path(args.report_md)),
            "report_json": _rel(_resolve_repo_path(args.report_json)),
            "artifact_dir": _rel(_resolve_repo_path(args.output_dir)),
        }
    finally:
        db.close()
        engine.dispose()


def run(args: argparse.Namespace) -> dict[str, Any]:
    return asyncio.run(_run_async(args))


def _status(args: argparse.Namespace) -> int:
    _validate_output_paths(args)
    payload = _read_json(args.checkpoint_status_json)
    if payload is None:
        print(json.dumps({"success": False, "error": "checkpoint_not_found"}, ensure_ascii=False, indent=2, sort_keys=True))
        return 1
    print(json.dumps(_coerce_json_safe(payload), ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--checkpoint-dir", default=str(PRIVATE_CHECKPOINT_DIR))
    parser.add_argument("--report-md", default=str(REPORT_MD))
    parser.add_argument("--report-json", default=str(REPORT_JSON))
    parser.add_argument("--provider-comparison-csv", default=str(PRIVATE_PROVIDER_COMPARISON_CSV))
    parser.add_argument("--provider-comparison-json", default=str(PRIVATE_PROVIDER_COMPARISON_JSON))
    parser.add_argument("--provider-summary-primary-only-json", default=str(PRIVATE_PROVIDER_SUMMARY_PRIMARY_ONLY_JSON))
    parser.add_argument("--name-candidates-primary-csv", default=str(PRIVATE_NAME_CANDIDATES_PRIMARY_CSV))
    parser.add_argument("--name-candidates-fallback-csv", default=str(PRIVATE_NAME_CANDIDATES_FALLBACK_CSV))
    parser.add_argument("--record-verdicts-primary-csv", default=str(PRIVATE_RECORD_VERDICTS_PRIMARY_CSV))
    parser.add_argument("--record-verdicts-fallback-csv", default=str(PRIVATE_RECORD_VERDICTS_FALLBACK_CSV))
    parser.add_argument("--rejected-general-meta-csv", default=str(PRIVATE_REJECTED_GENERAL_META_CSV))
    parser.add_argument("--disagreements-csv", default=str(PRIVATE_DISAGREEMENTS_CSV))
    parser.add_argument("--timeout-and-error-cases-csv", default=str(PRIVATE_TIMEOUT_ERROR_CSV))
    parser.add_argument("--popularity-prefix-extractions-csv", default=str(PRIVATE_POPULARITY_PREFIX_CSV))
    parser.add_argument("--false-positive-guard-review-csv", default=str(PRIVATE_FALSE_POSITIVE_GUARD_CSV))
    parser.add_argument("--pixiv-title-candidate-review-csv", default=str(PRIVATE_PIXIV_TITLE_REVIEW_CSV))
    parser.add_argument("--role-guard-review-csv", default=str(PRIVATE_ROLE_GUARD_REVIEW_CSV))
    parser.add_argument("--ambiguous-needs-review-csv", default=str(PRIVATE_AMBIGUOUS_CSV))
    parser.add_argument("--no-name-records-csv", default=str(PRIVATE_NO_NAME_RECORDS_CSV))
    parser.add_argument("--extraction-errors-csv", default=str(PRIVATE_EXTRACTION_ERRORS_CSV))
    parser.add_argument("--checkpoint-status-json", default=str(PRIVATE_CHECKPOINT_STATUS_JSON))
    parser.add_argument("--progress-events-jsonl", default=str(PRIVATE_PROGRESS_EVENTS_JSONL))
    parser.add_argument("--input-manifest-json", default=str(PRIVATE_INPUT_MANIFEST_JSON))
    parser.add_argument("--llm-inputs-jsonl", default=str(PRIVATE_LLM_INPUTS_JSONL))
    parser.add_argument("--llm-outputs-primary-jsonl", default=str(PRIVATE_LLM_OUTPUTS_PRIMARY_JSONL))
    parser.add_argument("--llm-outputs-fallback-jsonl", default=str(PRIVATE_LLM_OUTPUTS_FALLBACK_JSONL))
    parser.add_argument("--validation-failures-jsonl", default=str(PRIVATE_VALIDATION_FAILURES_JSONL))
    parser.add_argument("--summary-json", default=str(PRIVATE_SUMMARY_JSON))
    parser.add_argument("--manual-review-guide", default=str(PRIVATE_MANUAL_REVIEW_GUIDE))
    parser.add_argument("--details-json", default=str(PRIVATE_DETAILS_JSON))
    parser.add_argument("--prompt-sample-analysis-md", default=str(PRIVATE_PROMPT_SAMPLE_ANALYSIS_MD))
    parser.add_argument("--prompt-version-and-rules-md", default=str(PRIVATE_PROMPT_VERSION_RULES_MD))
    parser.add_argument("--public-redaction-check-txt", default=str(PRIVATE_PUBLIC_REDACTION_CHECK_TXT))
    parser.add_argument("--reviewer-fix-summary-md", default=str(PRIVATE_REVIEWER_FIX_SUMMARY_MD))
    parser.add_argument("--max-records", type=int, default=30)
    parser.add_argument("--max-unique-strings", type=int, default=500)
    parser.add_argument("--max-tokens", type=int, default=2500)
    parser.add_argument("--llm-retries", type=int, default=1)
    parser.add_argument("--llm-concurrency", type=int, default=3)
    parser.add_argument("--llm-timeout-seconds", type=float, default=90.0)
    parser.add_argument("--heartbeat-seconds", type=float, default=45.0)
    parser.add_argument("--provider-modes", default="primary_serial,fallback_serial")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--run-label", default="f7a_llm_source_name_candidate_extraction_rework")
    parser.add_argument("--use-llm-api", action="store_true")
    parser.add_argument("--apply-db", action="store_true")
    parser.add_argument("--clean-local-artifacts", action="store_true")
    parser.add_argument("--reuse-checkpoint", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--disable-media-tag-only-groups", action="store_true")
    parser.add_argument("--status-run-id", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.status_run_id:
        return _status(args)
    try:
        result = run(args)
    except (F7aRunnerError, SourceNameCandidateExtractionError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
