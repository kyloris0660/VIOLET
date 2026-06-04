"""Phase 4.4-P2R-F7a LLM-backed source name candidate extraction.

This runner consumes existing DB/source-layer metadata only. It does not run
providers, upload images, perform source enrichment, create SourceConcept rows,
or write Entity/media_tags truth paths.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import time
from dataclasses import asdict
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
from app.services.source_metadata_registry_service import canonical_source_key, normalize_source_text  # noqa: E402
from app.services.source_name_candidate_extraction_service import (  # noqa: E402
    EXTRACTOR_VERSION,
    PHASE,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    SourceCandidateInputGroup,
    SourceNameCandidateExtractionError,
    collect_source_candidate_input_groups,
    fallback_only_provider_from_settings,
    persist_extraction_bundle,
    run_extraction_sync,
)

PHASE_SLUG = "phase-4.4p2r-f7a-llm-source-name-candidates"
REPORT_MD = Path("docs/reports") / f"{PHASE_SLUG}.md"
REPORT_JSON = Path("docs/reports") / f"{PHASE_SLUG}-summary.json"
OUTPUT_DIR = Path(".local_manifests") / PHASE_SLUG

PRIVATE_NAME_CANDIDATES_CSV = OUTPUT_DIR / "name-candidates.csv"
PRIVATE_RECORD_VERDICTS_CSV = OUTPUT_DIR / "record-verdicts.csv"
PRIVATE_REJECTED_GENERAL_META_CSV = OUTPUT_DIR / "rejected-general-meta.csv"
PRIVATE_AMBIGUOUS_CSV = OUTPUT_DIR / "ambiguous-needs-review.csv"
PRIVATE_POPULARITY_PREFIX_CSV = OUTPUT_DIR / "popularity-prefix-extractions.csv"
PRIVATE_NO_NAME_RECORDS_CSV = OUTPUT_DIR / "no-name-records.csv"
PRIVATE_EXTRACTION_ERRORS_CSV = OUTPUT_DIR / "extraction-errors.csv"
PRIVATE_LLM_INPUTS_JSONL = OUTPUT_DIR / "llm-inputs.jsonl"
PRIVATE_LLM_OUTPUTS_JSONL = OUTPUT_DIR / "llm-outputs.jsonl"
PRIVATE_VALIDATION_FAILURES_JSONL = OUTPUT_DIR / "validation-failures.jsonl"
PRIVATE_SUMMARY_JSON = OUTPUT_DIR / "summary.json"
PRIVATE_MANUAL_REVIEW_GUIDE = OUTPUT_DIR / "manual-review-guide.md"
PRIVATE_DETAILS_JSON = OUTPUT_DIR / "details.json"

HARD_MAX_RECORDS = 1000
HARD_MAX_UNIQUE_STRINGS = 3000


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


def _require_under(path: Path, parent: Path, *, code: str) -> None:
    resolved = path.resolve()
    root = parent.resolve()
    if resolved != root and root not in resolved.parents:
        raise F7aRunnerError(code)


def _validate_output_paths(args: argparse.Namespace) -> None:
    output_dir = _resolve_repo_path(args.output_dir)
    _require_under(output_dir, ROOT / ".local_manifests", code="f7a_output_path_violation")
    if PHASE_SLUG not in output_dir.as_posix():
        raise F7aRunnerError("f7a_output_path_missing_phase_slug")
    for value in _private_paths(args):
        _require_under(_resolve_repo_path(value), output_dir, code="f7a_private_artifact_path_violation")
    _require_under(_resolve_repo_path(args.report_md), ROOT / "docs/reports", code="f7a_report_md_path_violation")
    _require_under(_resolve_repo_path(args.report_json), ROOT / "docs/reports", code="f7a_report_json_path_violation")


def _private_paths(args: argparse.Namespace) -> list[str]:
    return [
        args.name_candidates_csv,
        args.record_verdicts_csv,
        args.rejected_general_meta_csv,
        args.ambiguous_needs_review_csv,
        args.popularity_prefix_extractions_csv,
        args.no_name_records_csv,
        args.extraction_errors_csv,
        args.llm_inputs_jsonl,
        args.llm_outputs_jsonl,
        args.validation_failures_jsonl,
        args.summary_json,
        args.manual_review_guide,
        args.details_json,
    ]


def generate_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{PHASE_SLUG}-{timestamp}-{uuid4().hex[:8]}"


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


def _write_json(path: str | Path, payload: Any) -> None:
    _write_text(path, json.dumps(_coerce_json_safe(payload), ensure_ascii=False, indent=2, sort_keys=True, default=str))


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


def _load_cached_records(path: str | Path) -> dict[str, Mapping[str, Any]]:
    resolved = _resolve_repo_path(path)
    if not resolved.exists():
        return {}
    records: dict[str, Mapping[str, Any]] = {}
    for line in resolved.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = row.get("parsed_response") if isinstance(row, Mapping) else None
        if isinstance(payload, Mapping):
            if isinstance(payload.get("records"), list):
                for item in payload["records"]:
                    if isinstance(item, Mapping) and item.get("group_key"):
                        records[normalize_source_text(item["group_key"])] = item
            elif payload.get("group_key"):
                records[normalize_source_text(payload["group_key"])] = payload
    return records


def _fixture_groups() -> list[SourceCandidateInputGroup]:
    mona = "\u30e2\u30ca"
    genshin = "\u539f\u795e"
    return [
        SourceCandidateInputGroup(
            group_key="fixture:f7a:pixiv:popularity-prefix",
            provider="pixiv",
            data_type_label="fixture_or_mock",
            metadata_kind="f7a_fixture",
            tags=(
                {"raw_tag": f"{genshin}500users\u5165\u308a", "source_tag_kind": "provider_tag"},
                {"raw_tag": "\u30bb\u30fc\u30e9\u30fc\u670d", "source_tag_kind": "provider_tag"},
                {"raw_tag": f"{mona}({genshin})", "source_tag_kind": "provider_tag"},
            ),
            data_origin="fixture_or_test",
        ),
        SourceCandidateInputGroup(
            group_key="fixture:f7a:media-tags:barbara",
            provider="local_media_tags",
            media_tags=(
                {"tag_id": 1, "name": "barbara_(genshin_impact)", "category": "character", "source": "manual", "is_suggestion": False},
                {"tag_id": 2, "name": "genshin_impact", "category": "copyright", "source": "manual", "is_suggestion": False},
            ),
            data_origin="fixture_or_test",
        ),
    ]


def _maybe_add_fixture_supplement(groups: list[SourceCandidateInputGroup], args: argparse.Namespace) -> list[SourceCandidateInputGroup]:
    if not args.fixture_supplement:
        return groups
    existing_keys = {group.group_key for group in groups}
    result = list(groups)
    for group in _fixture_groups():
        if group.group_key not in existing_keys and len(result) < int(args.max_records):
            result.append(group)
    return result


def _public_summary(
    *,
    run_id: str,
    branch: str,
    head_sha: str,
    db_identity: Mapping[str, Any],
    input_summary: Mapping[str, Any],
    provider_summary: Mapping[str, Any],
    extraction_summary: Mapping[str, Any],
    db_write_summary: Mapping[str, Any],
    artifact_summary: Mapping[str, Any],
    timing: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "phase": PHASE,
        "title": "LLM-backed source name candidate extraction",
        "run_id": run_id,
        "branch": branch,
        "head_sha": head_sha,
        "extractor_version": EXTRACTOR_VERSION,
        "prompt_version": PROMPT_VERSION,
        "structured_output_schema_version": SCHEMA_VERSION,
        "db_identity": dict(db_identity),
        "input_summary": dict(input_summary),
        "llm_provider": dict(provider_summary),
        "timing": dict(timing),
        "extraction_summary": dict(extraction_summary),
        "db_write_summary": dict(db_write_summary),
        "artifacts": dict(artifact_summary),
        "safety": {
            "source_layer_only": True,
            "source_concept_linking": False,
            "entity_write": False,
            "entity_alias_write": False,
            "media_entity_candidate_write": False,
            "media_entity_assignment_write": False,
            "local_source_hint_write": False,
            "media_tags_mutation": False,
            "tag_translation_mutation": False,
            "provider_calls": False,
            "image_upload": False,
            "source_or_icloud_mutation": False,
            "push_main": False,
            "merge": False,
        },
    }


def _markdown_report(summary: Mapping[str, Any]) -> str:
    extraction = summary["extraction_summary"]
    candidates = extraction["candidate_counts"]
    rejected = extraction["rejected_counts"]
    timing = summary["timing"]
    db_write = summary["db_write_summary"]
    return "\n".join(
        [
            "# Phase 4.4-P2R-F7a: LLM-backed source name candidate extraction",
            "",
            "## Summary",
            "",
            "- Built a bounded source-layer name candidate extraction run over existing DB/source-layer metadata.",
            "- Output is an unconfirmed candidate pool only; no SourceConcept, Entity, media_tags, TagTranslation, or assignment truth paths are written.",
            "- Private review pack is stored under `.local_manifests` and is intentionally not committed.",
            "",
            "## Run",
            "",
            f"- Run ID: `{summary['run_id']}`",
            f"- Branch: `{summary['branch']}`",
            f"- Head SHA: `{summary['head_sha']}`",
            f"- Extractor version: `{summary['extractor_version']}`",
            f"- Prompt version: `{summary['prompt_version']}`",
            "",
            "## Data Sample",
            "",
            f"- Groups processed: `{extraction['input']['group_count']}`",
            f"- Unique raw strings: `{extraction['input']['unique_raw_string_count']}`",
            f"- Groups by provider: `{json.dumps(extraction['input']['groups_by_provider'], ensure_ascii=False, sort_keys=True)}`",
            f"- Groups by data origin: `{json.dumps(extraction['input']['groups_by_data_origin'], ensure_ascii=False, sort_keys=True)}`",
            "",
            "## LLM",
            "",
            f"- Provider mode: `{summary['llm_provider'].get('provider_mode')}`",
            f"- Uses fallback provider: `{summary['llm_provider'].get('uses_fallback_provider')}`",
            f"- Uses primary model: `{summary['llm_provider'].get('uses_primary_model')}`",
            f"- API call attempts: `{extraction['llm'].get('api_call_attempts', 0)}`",
            f"- Chunks attempted: `{extraction['llm'].get('api_chunks_attempted', 0)}`",
            f"- Cache hits: `{extraction['llm'].get('cache_hits', 0)}`",
            f"- Elapsed seconds: `{timing.get('elapsed_seconds')}`",
            f"- Cost estimate: `{timing.get('cost_estimate')}`",
            "",
            "## Extraction Results",
            "",
            f"- Record verdict counts: `{json.dumps(extraction['record_verdict_counts'], ensure_ascii=False, sort_keys=True)}`",
            f"- Candidate total: `{candidates['total']}`",
            f"- Candidate by role: `{json.dumps(candidates['by_role'], ensure_ascii=False, sort_keys=True)}`",
            f"- Candidate by status: `{json.dumps(candidates['by_status'], ensure_ascii=False, sort_keys=True)}`",
            f"- Candidate by action: `{json.dumps(candidates['by_action'], ensure_ascii=False, sort_keys=True)}`",
            f"- Popularity prefix extractions: `{candidates['popularity_prefix_extractions']}`",
            f"- Rejected tags: `{rejected['total']}`",
            f"- Rejected by reason: `{json.dumps(rejected['by_reason'], ensure_ascii=False, sort_keys=True)}`",
            f"- Meta tags: `{extraction['meta_counts']['total']}`",
            f"- Ambiguous items: `{extraction['ambiguous_count']}`",
            f"- Validation failures: `{extraction['validation_failure_count']}`",
            "",
            "## DB Write Summary",
            "",
            f"- Apply DB: `{db_write.get('apply')}`",
            f"- Forbidden truth table write count: `{db_write.get('forbidden_truth_table_write_count')}`",
            f"- Allowed table deltas: `{json.dumps(db_write.get('allowed_table_row_deltas', {}), ensure_ascii=False, sort_keys=True)}`",
            "",
            "## Review Pack",
            "",
            f"- Artifact directory: `{summary['artifacts']['artifact_dir']}`",
            f"- Manual review guide: `{summary['artifacts']['manual_review_guide']}`",
            f"- Name candidates CSV: `{summary['artifacts']['name_candidates_csv']}`",
            f"- Record verdicts CSV: `{summary['artifacts']['record_verdicts_csv']}`",
            f"- LLM inputs JSONL: `{summary['artifacts']['llm_inputs_jsonl']}`",
            f"- LLM outputs JSONL: `{summary['artifacts']['llm_outputs_jsonl']}`",
            "",
            "## Safety Confirmation",
            "",
            "- No SourceConcept linking.",
            "- No Entity, EntityAlias, EntityEvidence, MediaEntityCandidate, MediaEntityAssignment, LocalSourceHint, confirmed assignment, media_tags, or TagTranslation writes.",
            "- No provider/gallery-dl/source enrichment run.",
            "- No image upload and no source/iCloud/app-managed storage mutation.",
            "- No push to main and no merge.",
            "",
            "## Next Step",
            "",
            "Review the private candidate pack before deciding whether F7b SourceConcept linking is ready.",
            "",
        ]
    )


def _manual_review_guide(summary: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# F7a manual review guide",
            "",
            "Review order:",
            "",
            "1. `record-verdicts.csv` for no-silent-failure coverage.",
            "2. `name-candidates.csv` for obvious name/work/artist recall and multilingual preservation.",
            "3. `rejected-general-meta.csv` for false positive rejection quality.",
            "4. `popularity-prefix-extractions.csv` for Pixiv popularity suffix stripping.",
            "5. `ambiguous-needs-review.csv` and `no-name-records.csv` for over-rejection and hard cases.",
            "6. `llm-inputs.jsonl` / `llm-outputs.jsonl` for exact prompt/response audit.",
            "",
            "Do not treat any row as Entity truth. F7a candidates are source-layer evidence only.",
            "",
            f"Run ID: `{summary['run_id']}`",
            f"Candidate total: `{summary['extraction_summary']['candidate_counts']['total']}`",
            f"Popularity prefix extractions: `{summary['extraction_summary']['candidate_counts']['popularity_prefix_extractions']}`",
            "",
        ]
    )


def _artifact_summary(args: argparse.Namespace) -> dict[str, str]:
    return {
        "artifact_dir": _rel(_resolve_repo_path(args.output_dir)),
        "name_candidates_csv": _rel(_resolve_repo_path(args.name_candidates_csv)),
        "record_verdicts_csv": _rel(_resolve_repo_path(args.record_verdicts_csv)),
        "rejected_general_meta_csv": _rel(_resolve_repo_path(args.rejected_general_meta_csv)),
        "ambiguous_needs_review_csv": _rel(_resolve_repo_path(args.ambiguous_needs_review_csv)),
        "popularity_prefix_extractions_csv": _rel(_resolve_repo_path(args.popularity_prefix_extractions_csv)),
        "no_name_records_csv": _rel(_resolve_repo_path(args.no_name_records_csv)),
        "extraction_errors_csv": _rel(_resolve_repo_path(args.extraction_errors_csv)),
        "llm_inputs_jsonl": _rel(_resolve_repo_path(args.llm_inputs_jsonl)),
        "llm_outputs_jsonl": _rel(_resolve_repo_path(args.llm_outputs_jsonl)),
        "validation_failures_jsonl": _rel(_resolve_repo_path(args.validation_failures_jsonl)),
        "private_summary_json": _rel(_resolve_repo_path(args.summary_json)),
        "manual_review_guide": _rel(_resolve_repo_path(args.manual_review_guide)),
        "details_json": _rel(_resolve_repo_path(args.details_json)),
        "public_report_md": _rel(_resolve_repo_path(args.report_md)),
        "public_report_json": _rel(_resolve_repo_path(args.report_json)),
    }


def _git_value(command: Sequence[str]) -> str:
    import subprocess

    proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=True)
    return proc.stdout.strip()


def write_artifacts(args: argparse.Namespace, bundle, public_summary: Mapping[str, Any]) -> None:
    _write_text(args.name_candidates_csv, _csv(asdict(row) for row in bundle.candidates))
    _write_text(args.record_verdicts_csv, _csv(asdict(row) for row in bundle.record_verdicts))
    _write_text(args.rejected_general_meta_csv, _csv(asdict(row) for row in bundle.rejected_tags))
    _write_text(args.ambiguous_needs_review_csv, _csv(asdict(row) for row in bundle.ambiguous_items))
    _write_text(
        args.popularity_prefix_extractions_csv,
        _csv(row for row in (asdict(candidate) for candidate in bundle.candidates) if row.get("extraction_action") == "popularity_suffix_stripped"),
    )
    _write_text(
        args.no_name_records_csv,
        _csv(row for row in (asdict(verdict) for verdict in bundle.record_verdicts) if row.get("no_name_reason")),
    )
    _write_text(
        args.extraction_errors_csv,
        _csv(row for row in (asdict(verdict) for verdict in bundle.record_verdicts) if str(row.get("extraction_verdict", "")).startswith("extraction_error")),
    )
    _write_text(args.llm_inputs_jsonl, _jsonl(bundle.llm_inputs))
    _write_text(args.llm_outputs_jsonl, _jsonl(bundle.llm_outputs))
    _write_text(args.validation_failures_jsonl, _jsonl(bundle.validation_failures))
    _write_json(args.summary_json, public_summary)
    _write_json(
        args.details_json,
        {
            "run_id": bundle.run_id,
            "groups": [asdict(group) for group in bundle.groups],
            "record_verdicts": [asdict(row) for row in bundle.record_verdicts],
            "candidates": [asdict(row) for row in bundle.candidates],
            "rejected_tags": [asdict(row) for row in bundle.rejected_tags],
            "meta_tags": [asdict(row) for row in bundle.meta_tags],
            "ambiguous_items": [asdict(row) for row in bundle.ambiguous_items],
            "summary_public": public_summary,
        },
    )
    _write_text(args.manual_review_guide, _manual_review_guide(public_summary))
    _write_json(args.report_json, public_summary)
    _write_text(args.report_md, _markdown_report(public_summary))


def run(args: argparse.Namespace) -> dict[str, Any]:
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

    provider, provider_summary = fallback_only_provider_from_settings()
    if provider is None:
        raise F7aRunnerError(f"llm_provider_unavailable:{provider_summary.get('unavailable_reason')}")

    run_id = normalize_source_text(args.run_id) or generate_run_id()
    run_label = normalize_source_text(args.run_label) or "f7a_llm_source_name_candidate_extraction"
    branch = _git_value(["git", "branch", "--show-current"])
    head_sha = _git_value(["git", "rev-parse", "HEAD"])
    start = time.perf_counter()
    engine, db = _connect_db()
    try:
        db_identity = _db_identity(db)
        groups, input_summary = collect_source_candidate_input_groups(
            db,
            max_records=int(args.max_records),
            max_unique_strings=int(args.max_unique_strings),
            include_media_tag_only_groups=not args.disable_media_tag_only_groups,
        )
        groups = _maybe_add_fixture_supplement(groups, args)
        if not groups:
            raise F7aRunnerError("no_candidate_input_groups_available")
        cached_records = _load_cached_records(args.llm_outputs_jsonl) if args.reuse_llm_cache else {}
        bundle = run_extraction_sync(
            provider,
            groups,
            run_id=run_id,
            run_label=run_label,
            chunk_size=int(args.chunk_size),
            retries=int(args.llm_retries),
            max_tokens=int(args.max_tokens),
            cached_records_by_group_key=cached_records,
        )
        db_write_summary = persist_extraction_bundle(
            db,
            bundle,
            apply=bool(args.apply_db),
            provider_summary=provider_summary,
            input_scope=input_summary,
        )
        elapsed = round(time.perf_counter() - start, 3)
        timing = {
            "elapsed_seconds": elapsed,
            "cost_estimate": "not_available_from_provider_response",
            "api_call_attempts": bundle.summary.get("llm", {}).get("api_call_attempts", 0),
        }
        artifacts = _artifact_summary(args)
        public_summary = _public_summary(
            run_id=run_id,
            branch=branch,
            head_sha=head_sha,
            db_identity=db_identity,
            input_summary={**input_summary, "fixture_supplement": bool(args.fixture_supplement)},
            provider_summary=provider_summary,
            extraction_summary=bundle.summary,
            db_write_summary=db_write_summary,
            artifact_summary=artifacts,
            timing=timing,
        )
        write_artifacts(args, bundle, public_summary)
        return {
            "success": True,
            "run_id": run_id,
            "branch": branch,
            "head_sha": head_sha,
            "groups_processed": bundle.summary["input"]["group_count"],
            "unique_raw_strings": bundle.summary["input"]["unique_raw_string_count"],
            "candidate_total": bundle.summary["candidate_counts"]["total"],
            "popularity_prefix_extractions": bundle.summary["candidate_counts"]["popularity_prefix_extractions"],
            "forbidden_truth_table_write_count": db_write_summary.get("forbidden_truth_table_write_count"),
            "report_md": _rel(_resolve_repo_path(args.report_md)),
            "report_json": _rel(_resolve_repo_path(args.report_json)),
            "artifact_dir": _rel(_resolve_repo_path(args.output_dir)),
        }
    finally:
        db.close()
        engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--report-md", default=str(REPORT_MD))
    parser.add_argument("--report-json", default=str(REPORT_JSON))
    parser.add_argument("--name-candidates-csv", default=str(PRIVATE_NAME_CANDIDATES_CSV))
    parser.add_argument("--record-verdicts-csv", default=str(PRIVATE_RECORD_VERDICTS_CSV))
    parser.add_argument("--rejected-general-meta-csv", default=str(PRIVATE_REJECTED_GENERAL_META_CSV))
    parser.add_argument("--ambiguous-needs-review-csv", default=str(PRIVATE_AMBIGUOUS_CSV))
    parser.add_argument("--popularity-prefix-extractions-csv", default=str(PRIVATE_POPULARITY_PREFIX_CSV))
    parser.add_argument("--no-name-records-csv", default=str(PRIVATE_NO_NAME_RECORDS_CSV))
    parser.add_argument("--extraction-errors-csv", default=str(PRIVATE_EXTRACTION_ERRORS_CSV))
    parser.add_argument("--llm-inputs-jsonl", default=str(PRIVATE_LLM_INPUTS_JSONL))
    parser.add_argument("--llm-outputs-jsonl", default=str(PRIVATE_LLM_OUTPUTS_JSONL))
    parser.add_argument("--validation-failures-jsonl", default=str(PRIVATE_VALIDATION_FAILURES_JSONL))
    parser.add_argument("--summary-json", default=str(PRIVATE_SUMMARY_JSON))
    parser.add_argument("--manual-review-guide", default=str(PRIVATE_MANUAL_REVIEW_GUIDE))
    parser.add_argument("--details-json", default=str(PRIVATE_DETAILS_JSON))
    parser.add_argument("--max-records", type=int, default=200)
    parser.add_argument("--max-unique-strings", type=int, default=500)
    parser.add_argument("--chunk-size", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=6000)
    parser.add_argument("--llm-retries", type=int, default=1)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--run-label", default="f7a_llm_source_name_candidate_extraction")
    parser.add_argument("--use-llm-api", action="store_true")
    parser.add_argument("--apply-db", action="store_true")
    parser.add_argument("--clean-local-artifacts", action="store_true")
    parser.add_argument("--reuse-llm-cache", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--fixture-supplement", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--disable-media-tag-only-groups", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run(args)
    except (F7aRunnerError, SourceNameCandidateExtractionError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
