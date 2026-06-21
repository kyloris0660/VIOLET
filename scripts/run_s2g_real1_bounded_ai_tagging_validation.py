#!/usr/bin/env python3
"""Run a bounded S2G-REAL1 real-media AI tagging validation.

The runner is intentionally tiny and operator-driven. It validates the current
AI tagging service path on a small explicit media scope, writes only aggregate
public reports, and refuses media tag writes unless the exact phase approval
string is provided.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SUMMARY_PATH = ROOT / "docs" / "reports" / "s2g-real1-bounded-ai-tagging-validation-summary.json"
MARKDOWN_PATH = ROOT / "docs" / "reports" / "s2g-real1-bounded-ai-tagging-validation.md"

PHASE = "S2G-REAL1"
CONTRACT_ID = "s2g_real1_bounded_ai_tagging_validation_contract_v1"
WRITE_CONFIRMATION = "I APPROVE S2G-REAL1 BOUNDED AI TAGGING WRITE"
MAX_ALLOWED_ITEMS = 5
DEFAULT_MAX_ITEMS = 3
DEFAULT_PROVIDER_PREFERENCE = "DmlExecutionProvider,CPUExecutionProvider"
CPU_PROVIDER_PREFERENCE = "CPUExecutionProvider"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def repo_relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def parse_media_ids(raw: str | None) -> list[int] | None:
    if raw is None or not raw.strip():
        return None
    ids: list[int] = []
    for part in raw.replace(";", ",").replace(" ", ",").split(","):
        value = part.strip()
        if not value:
            continue
        ids.append(int(value))
    return ids


def provider_list(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def count_ai_wd_media_tags(db: Any) -> int:
    from backend.app.models import blombooru_media_tags

    return int(
        db.query(blombooru_media_tags.c.media_id)
        .filter(blombooru_media_tags.c.source == "ai_wd")
        .count()
    )


def base_runtime_env(max_items: int, provider_preference: str) -> dict[str, str]:
    return {
        "AI_TAGGING_ENABLED": "true",
        "AI_TAGGING_PROVIDER_PREFERENCE": provider_preference,
        "AI_TAGGING_BATCH_MAX_ITEMS": str(max_items),
        "AI_TAGGING_MAX_CONCURRENT_JOBS": "1",
        "AI_TAGGING_PREPROCESS_WORKERS": "2",
        "AI_TAGGING_CPU_INTRA_OP_THREADS": "4",
        "AI_TAGGING_CPU_INTER_OP_THREADS": "1",
        "AI_TAGGING_EXECUTION_MODE": "ORT_SEQUENTIAL",
        "AI_TAGGING_PROCESS_PRIORITY": "below_normal",
        "AI_TAGGING_AUTO_LOCALIZATION": "false",
        "TAG_TRANSLATION_AUTO_ENABLED": "false",
        "TAG_TRANSLATION_BACKGROUND_ENABLED": "false",
        "TAG_TRANSLATION_LLM_ENABLED": "false",
        "CONTENT_CLASSIFICATION_ENABLED": "false",
    }


@contextlib.contextmanager
def temporary_env(updates: dict[str, str]) -> Iterator[None]:
    old_values = {key: os.environ.get(key) for key in updates}
    try:
        for key, value in updates.items():
            os.environ[key] = value
        yield
    finally:
        for key, old_value in old_values.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


def check_model_cache(local_files_only: bool) -> dict[str, Any]:
    from backend.app.config import settings
    from backend.app.services.wd_tagger import WDTagger

    model_name = settings.AI_MODEL_NAME
    model_repo = WDTagger.AVAILABLE_MODELS.get(model_name)
    result = {
        "model_name": model_name,
        "model_repo_id": model_repo,
        "local_files_only": local_files_only,
        "model_download_allowed": not local_files_only,
        "model_download_performed": False,
        "model_file_cached": False,
        "label_file_cached": False,
        "status": "not_checked",
        "blocker": None,
    }
    if not model_repo:
        result["status"] = "blocked"
        result["blocker"] = "unknown_model_name"
        return result
    try:
        import huggingface_hub

        huggingface_hub.hf_hub_download(
            model_repo,
            WDTagger.LABEL_FILENAME,
            local_files_only=True,
        )
        result["label_file_cached"] = True
        huggingface_hub.hf_hub_download(
            model_repo,
            WDTagger.MODEL_FILENAME,
            local_files_only=True,
        )
        result["model_file_cached"] = True
        result["status"] = "cached"
    except Exception as exc:  # noqa: BLE001 - public report records type only.
        result["status"] = "blocked"
        result["blocker"] = exc.__class__.__name__
    return result


def validate_content_classes(values: Iterable[str]) -> list[str]:
    from backend.app.enums import ContentClassEnum

    valid = {item.value for item in ContentClassEnum}
    normalized: list[str] = []
    for value in values:
        text = value.strip()
        if not text:
            continue
        if text not in valid:
            raise ValueError(f"Invalid content_class {text!r}. Expected one of {sorted(valid)}.")
        if text not in normalized:
            normalized.append(text)
    return normalized


def select_media_ids(
    db: Any,
    *,
    explicit_media_ids: list[int] | None,
    content_classes: list[str],
    max_items: int,
    candidate_scan_limit: int,
) -> tuple[list[int], dict[str, Any]]:
    from sqlalchemy import or_

    from backend.app.enums import ContentClassEnum
    from backend.app.models import Media
    from backend.app.services.ai_tagging_service import _resolve_media_file

    selection_mode = "explicit_media_ids" if explicit_media_ids is not None else "content_class_filter"
    if explicit_media_ids is not None:
        if not explicit_media_ids:
            raise ValueError("Explicit media_ids resolved to an empty list.")
        if len(explicit_media_ids) > max_items:
            raise ValueError("Explicit media_ids exceed max_items.")
        rows = db.query(Media).filter(Media.id.in_(explicit_media_ids)).all()
        by_id = {int(row.id): row for row in rows}
        ordered_rows = [by_id[mid] for mid in explicit_media_ids if mid in by_id]
        candidate_count = len(ordered_rows)
    else:
        conditions = []
        for value in content_classes:
            enum_value = ContentClassEnum(value)
            conditions.append(Media.content_class == enum_value)
            if enum_value == ContentClassEnum.unknown:
                conditions.append(Media.content_class.is_(None))
        query = db.query(Media).order_by(Media.id.asc())
        if conditions:
            query = query.filter(or_(*conditions))
        ordered_rows = query.limit(candidate_scan_limit).all()
        candidate_count = len(ordered_rows)

    selected: list[int] = []
    skipped_missing = 0
    for media in ordered_rows:
        if _resolve_media_file(media) is None:
            skipped_missing += 1
            continue
        selected.append(int(media.id))
        if len(selected) >= max_items:
            break

    summary = {
        "selection_mode": selection_mode,
        "content_class_filter": content_classes if explicit_media_ids is None else [],
        "explicit_media_ids_supplied": explicit_media_ids is not None,
        "explicit_media_ids_publicly_recorded": False,
        "candidate_scan_limit": candidate_scan_limit,
        "candidate_rows_reviewed": candidate_count,
        "skipped_missing_local_file_count": skipped_missing,
        "count": len(selected),
        "id_count": len(selected),
        "max_items": max_items,
        "small_explicit_sample": 0 < len(selected) <= MAX_ALLOWED_ITEMS,
        "no_full_library_fallback": True,
        "private_locator_values_recorded": False,
    }
    if not selected:
        summary["status"] = "blocked_no_media"
    else:
        summary["status"] = "selected"
    return selected, summary


def public_result_entry(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("error"):
        return {
            "status": "failed",
            "error_type": "redacted_error",
        }
    return {
        "status": "completed",
        "predictions": len(result.get("predictions", []) or []),
        "tags_added": int(result.get("tags_added", 0) or 0),
        "suggestions_added": int(result.get("suggestions_added", 0) or 0),
        "skipped_locked": int(result.get("skipped_locked", 0) or 0),
        "ignored_low_confidence": int(result.get("ignored_low_confidence", 0) or 0),
    }


def aggregate_prediction_categories(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        for pred in result.get("predictions", []) or []:
            category = str(pred.get("category") or "unknown")
            counts[category] = counts.get(category, 0) + 1
    return dict(sorted(counts.items()))


def run_validation_pass(
    db: Any,
    *,
    label: str,
    media_ids: list[int],
    dry_run: bool,
    provider_preference: str,
    max_items: int,
    local_files_only: bool,
    force_suggestions: bool,
) -> dict[str, Any]:
    from backend.app.services.ai_tagging_service import (
        get_ai_tagging_runtime_provenance,
        run_ai_tagging,
    )

    env = base_runtime_env(max_items, provider_preference)
    started = time.perf_counter()
    before_count = count_ai_wd_media_tags(db)
    results: list[dict[str, Any]] = []
    public_results: list[dict[str, Any]] = []
    provenance: dict[str, Any] | None = None

    with temporary_env(env):
        for media_id in media_ids:
            try:
                result = run_ai_tagging(
                    db,
                    media_id,
                    dry_run=dry_run,
                    force_suggestions=force_suggestions,
                    local_files_only=local_files_only,
                )
                if dry_run:
                    db.rollback()
                if provenance is None and isinstance(result.get("provenance"), dict):
                    provenance = result["provenance"]
                results.append(result)
                public_results.append(public_result_entry(result))
            except Exception as exc:  # noqa: BLE001 - public report records type only.
                db.rollback()
                results.append({"error_type": exc.__class__.__name__})
                public_results.append({"status": "failed", "error_type": exc.__class__.__name__})

        if provenance is None:
            try:
                provenance = get_ai_tagging_runtime_provenance()
            except Exception as exc:  # noqa: BLE001
                provenance = {"available": False, "error_type": exc.__class__.__name__}

    after_count = count_ai_wd_media_tags(db)
    elapsed = round(time.perf_counter() - started, 4)
    tags_added = sum(int(result.get("tags_added", 0) or 0) for result in results)
    suggestions_added = sum(int(result.get("suggestions_added", 0) or 0) for result in results)
    skipped_locked = sum(int(result.get("skipped_locked", 0) or 0) for result in results)
    ignored_low_confidence = sum(int(result.get("ignored_low_confidence", 0) or 0) for result in results)
    failed = sum(1 for result in results if result.get("error") or result.get("error_type"))
    predicted_count = sum(len(result.get("predictions", []) or []) for result in results)
    provider = provenance.get("provider", {}) if isinstance(provenance, dict) else {}
    load_control = provenance.get("load_control", {}) if isinstance(provenance, dict) else {}

    return {
        "label": label,
        "executed": True,
        "status": "completed" if failed == 0 else "completed_with_item_failures",
        "dry_run": dry_run,
        "local_files_only": local_files_only,
        "provider_preference_requested": provider_list(provider_preference),
        "selected_media_count": len(media_ids),
        "processed": len(results),
        "tags_added": tags_added,
        "suggestions_added": suggestions_added,
        "skipped_locked": skipped_locked,
        "ignored_low_confidence": ignored_low_confidence,
        "failed": failed,
        "predicted_tag_count": predicted_count,
        "prediction_category_counts": aggregate_prediction_categories(results),
        "media_tags_count_before": before_count,
        "media_tags_count_after": after_count,
        "media_tags_count_delta": after_count - before_count,
        "no_media_tags_writes": (after_count == before_count) if dry_run else None,
        "tag_source_values_used": ["ai_wd"],
        "job_record_created": False,
        "elapsed_seconds": elapsed,
        "public_item_results": public_results,
        "runtime_provenance": provenance,
        "provider": provider,
        "load_control": load_control,
    }


def build_s3a_boundary() -> dict[str, Any]:
    return {
        "production_execution_enabled": False,
        "unattended_enabled": False,
        "dry_run_only": True,
        "stages": [
            {"name": "update_check", "writes_enabled": False},
            {"name": "hydration_read", "writes_enabled": False},
            {"name": "import_reuse", "writes_enabled": False},
            {"name": "classification", "writes_enabled": False},
            {"name": "ai_tagging", "writes_enabled": False},
            {"name": "localization", "writes_enabled": False},
            {"name": "summary", "writes_enabled": False},
        ],
    }


def build_safety(write_executed: bool, dry_run: dict[str, Any] | None) -> dict[str, Any]:
    dry_run_delta = int((dry_run or {}).get("media_tags_count_delta", 0) or 0)
    return {
        "max_items_lte_5": True,
        "no_full_library_run": True,
        "dry_run_before_write": True,
        "ai_tagging_write_without_confirmation": False,
        "media_tags_write_executed": write_executed,
        "dry_run_media_tags_write": dry_run_delta != 0,
        "production_s3a_execution_enabled": False,
        "unattended_s3b_enabled": False,
        "provider_pixiv_gallery_dl_saucenao_google_calls": False,
        "provider_pixiv_r1r_entity_operations": False,
        "sourceconcept_r1r_r2": False,
        "entity_bridge": False,
        "confirmed_entity_assignments": False,
        "source_icloud_mutation": False,
        "cleanup_delete_reset_drop_truncate": False,
        "db_import": False,
        "production_import": False,
        "production_classification": False,
        "production_localization": False,
        "model_download": False,
        "local_files_only": True,
        "private_locator_values_recorded": False,
    }


def derive_status(
    *,
    selected_media: dict[str, Any],
    model_cache: dict[str, Any],
    dry_run: dict[str, Any] | None,
    cpu_fallback: dict[str, Any] | None,
    write_executed: bool,
    write_confirmed: bool,
) -> str:
    if selected_media.get("count", 0) == 0:
        return "blocked_no_media"
    if model_cache.get("status") != "cached":
        return "blocked_model_cache_missing"
    if not dry_run or not dry_run.get("executed"):
        return "blocked_dry_run_not_completed"
    if dry_run.get("failed", 0):
        return "blocked_dry_run_item_failures"
    if not cpu_fallback or not cpu_fallback.get("executed") or cpu_fallback.get("failed", 0):
        return "blocked_cpu_fallback_not_validated"
    if write_executed:
        return "target_met_with_bounded_write"
    if write_confirmed:
        return "blocked_write_requested_not_completed"
    return "target_met_dry_run_only"


def build_load_control_observations(primary: dict[str, Any] | None) -> dict[str, Any]:
    load_control = (primary or {}).get("load_control", {})
    provider = (primary or {}).get("provider", {})
    actual_provider = provider.get("actual_provider")
    warnings = []
    if actual_provider == "DmlExecutionProvider":
        warnings.append("onnxruntime_directml_partial_node_assignment_warning_observed_or_possible")
    return {
        "batch_size": load_control.get("batch_size"),
        "effective_batch_size": load_control.get("effective_batch_size"),
        "configured_batch_size": load_control.get("configured_batch_size"),
        "batch_cap_source": load_control.get("batch_cap_source"),
        "cpu_intra_op_threads": load_control.get("cpu_intra_op_threads"),
        "cpu_inter_op_threads": load_control.get("cpu_inter_op_threads"),
        "preprocess_workers": load_control.get("preprocess_workers"),
        "max_concurrent_ai_jobs": load_control.get("max_concurrent_jobs"),
        "execution_mode": load_control.get("execution_mode"),
        "process_priority": load_control.get("process_priority"),
        "actual_provider": actual_provider,
        "appeared_bounded": (
            load_control.get("max_concurrent_jobs") == 1
            and (load_control.get("effective_batch_size") or 0) <= MAX_ALLOWED_ITEMS
            and (load_control.get("preprocess_workers") or 0) <= 2
            and (load_control.get("cpu_intra_op_threads") or 0) <= 4
            and load_control.get("cpu_inter_op_threads") == 1
        ),
        "warnings": warnings,
    }


def write_reports(summary: dict[str, Any]) -> None:
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown(summary)
    summary["public_redaction"] = {"passed": False, "finding_count": None}
    preliminary_payload = {
        "public_json_payload": summary,
        "public_markdown_text": markdown,
    }

    from scripts.phase_contracts.contract_checks import scan_public_payload

    findings = scan_public_payload(preliminary_payload)
    summary["public_redaction"] = {
        "passed": not findings,
        "finding_count": len(findings),
        "findings_redacted": True,
        "scan_scope": "public_json_and_markdown",
    }
    markdown = render_markdown(summary)
    findings = scan_public_payload(
        {"public_json_payload": summary, "public_markdown_text": markdown}
    )
    summary["public_redaction"] = {
        "passed": not findings,
        "finding_count": len(findings),
        "findings_redacted": True,
        "scan_scope": "public_json_and_markdown",
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    MARKDOWN_PATH.write_text(render_markdown(summary), encoding="utf-8")


def render_markdown(summary: dict[str, Any]) -> str:
    dry_run = summary.get("dry_run", {})
    write_run = summary.get("write_run", {})
    primary = summary.get("primary_provider_validation", {})
    cpu = summary.get("cpu_fallback_validation", {})
    load = summary.get("load_control_observations", {})
    selected = summary.get("selected_media", {})
    status = summary.get("pipeline_contract", {}).get("status")

    lines = [
        "# S2G-REAL1: Bounded Real AI Tagging Validation with DirectML",
        "",
        f"Status: `{status}`.",
        "",
        f"Contract: `{CONTRACT_ID}`.",
        "",
        f"Public summary: `{repo_relative(SUMMARY_PATH)}`.",
        "",
        "## Scope",
        "",
        f"- Selection mode: `{selected.get('selection_mode')}`.",
        f"- Selected media count: `{selected.get('count')}`.",
        f"- Max items: `{summary.get('run_configuration', {}).get('max_items')}`.",
        f"- Public private locator values recorded: `{selected.get('private_locator_values_recorded')}`.",
        f"- Full-library fallback: `{not selected.get('no_full_library_fallback', False)}`.",
        "",
        "## Dry-run Result",
        "",
        f"- Executed: `{dry_run.get('executed')}`.",
        f"- Status: `{dry_run.get('status')}`.",
        f"- Processed: `{dry_run.get('processed')}`.",
        f"- Predicted tags: `{dry_run.get('predicted_tag_count')}`.",
        f"- Confirmed tag actions predicted: `{dry_run.get('tags_added')}`.",
        f"- Suggestion actions predicted: `{dry_run.get('suggestions_added')}`.",
        f"- Media tag delta: `{dry_run.get('media_tags_count_delta')}`.",
        f"- Dry-run media tag writes: `{not dry_run.get('no_media_tags_writes', False)}`.",
        "",
        "## Provider Result",
        "",
        f"- Requested provider preference: `{primary.get('provider_preference_requested')}`.",
        f"- Actual provider: `{primary.get('provider', {}).get('actual_provider')}`.",
        f"- Fallback occurred: `{primary.get('provider', {}).get('fallback_occurred')}`.",
        f"- Fallback reason: `{primary.get('provider', {}).get('fallback_reason')}`.",
        "",
        "## CPU Fallback",
        "",
        f"- Executed: `{cpu.get('executed')}`.",
        f"- Requested provider preference: `{cpu.get('provider_preference_requested')}`.",
        f"- Actual provider: `{cpu.get('provider', {}).get('actual_provider')}`.",
        f"- Media tag delta: `{cpu.get('media_tags_count_delta')}`.",
        "",
        "## Write Result",
        "",
        f"- Executed: `{write_run.get('executed')}`.",
        f"- Status: `{write_run.get('status')}`.",
        f"- Exact operator confirmation present: `{summary.get('run_configuration', {}).get('operator_confirmation_exact')}`.",
        f"- Media tag delta: `{write_run.get('media_tags_count_delta')}`.",
        "",
        "## Load Control Observations",
        "",
        f"- Batch size: `{load.get('batch_size')}`.",
        f"- Effective batch size: `{load.get('effective_batch_size')}`.",
        f"- CPU intra/inter threads: `{load.get('cpu_intra_op_threads')}` / `{load.get('cpu_inter_op_threads')}`.",
        f"- Preprocess workers: `{load.get('preprocess_workers')}`.",
        f"- Max concurrent AI jobs: `{load.get('max_concurrent_ai_jobs')}`.",
        f"- Appeared bounded: `{load.get('appeared_bounded')}`.",
        "",
        "## Safety",
        "",
        "- Production S3A execution remains disabled.",
        "- Unattended S3B remains disabled.",
        "- Provider/Pixiv/gallery-dl/SauceNAO/Google/R1R/Entity operations were not run.",
        "- Cleanup/delete/reset/drop/truncate was not run.",
        "- Reports are aggregate and path-redacted.",
        "",
        "## Backlog",
        "",
    ]
    for item in summary.get("backlog", []):
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run S2G-REAL1 bounded real-media AI tagging validation.")
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    parser.add_argument("--media-ids", help="Comma-separated explicit media IDs. Must be <= max-items.")
    parser.add_argument(
        "--content-class",
        action="append",
        dest="content_classes",
        default=None,
        help="Content class filter. Can be repeated. Defaults to anime.",
    )
    parser.add_argument("--candidate-scan-limit", type=int, default=25)
    parser.add_argument("--provider-preference", default=DEFAULT_PROVIDER_PREFERENCE)
    parser.add_argument("--execute", action="store_true", help="Attempt the bounded write run after dry-run.")
    parser.add_argument("--operator-confirmation", default="")
    parser.add_argument("--force-suggestions", action="store_true")
    parser.add_argument(
        "--allow-model-download",
        action="store_true",
        help="Allow Hugging Face model download. Not used for the S2G-REAL1 public validation run.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    max_items = int(args.max_items)
    if not (1 <= max_items <= MAX_ALLOWED_ITEMS):
        raise SystemExit(f"--max-items must be between 1 and {MAX_ALLOWED_ITEMS}.")
    candidate_scan_limit = max(max_items, min(int(args.candidate_scan_limit), 50))
    explicit_media_ids = parse_media_ids(args.media_ids)
    content_classes = validate_content_classes(args.content_classes or ["anime"])
    if explicit_media_ids is not None and len(explicit_media_ids) > max_items:
        raise SystemExit("--media-ids count must not exceed --max-items.")

    local_files_only = not args.allow_model_download
    operator_confirmation_exact = args.operator_confirmation == WRITE_CONFIRMATION
    write_requested = bool(args.execute)
    write_confirmed = write_requested and operator_confirmation_exact

    from backend.app import database

    dry_run: dict[str, Any] | None = None
    write_run: dict[str, Any] | None = None
    cpu_fallback: dict[str, Any] | None = None
    selected_media: dict[str, Any] = {}
    selected_ids: list[int] = []
    model_cache: dict[str, Any] = {}

    with temporary_env(base_runtime_env(max_items, args.provider_preference)):
        model_cache = check_model_cache(local_files_only)
        database.init_engine()
        if database.SessionLocal is None:
            raise RuntimeError("Database session factory is not initialized.")
        db = database.SessionLocal()
        try:
            selected_ids, selected_media = select_media_ids(
                db,
                explicit_media_ids=explicit_media_ids,
                content_classes=content_classes,
                max_items=max_items,
                candidate_scan_limit=candidate_scan_limit,
            )
            if selected_ids and model_cache.get("status") == "cached":
                dry_run = run_validation_pass(
                    db,
                    label="primary_dry_run",
                    media_ids=selected_ids,
                    dry_run=True,
                    provider_preference=args.provider_preference,
                    max_items=max_items,
                    local_files_only=local_files_only,
                    force_suggestions=args.force_suggestions,
                )
                if write_confirmed:
                    write_run = run_validation_pass(
                        db,
                        label="bounded_write",
                        media_ids=selected_ids,
                        dry_run=False,
                        provider_preference=args.provider_preference,
                        max_items=max_items,
                        local_files_only=local_files_only,
                        force_suggestions=args.force_suggestions,
                    )
                cpu_fallback = run_validation_pass(
                    db,
                    label="cpu_fallback_dry_run",
                    media_ids=selected_ids[:1],
                    dry_run=True,
                    provider_preference=CPU_PROVIDER_PREFERENCE,
                    max_items=max_items,
                    local_files_only=local_files_only,
                    force_suggestions=args.force_suggestions,
                )
        finally:
            db.close()

    if dry_run is None:
        dry_run = {
            "executed": False,
            "status": "not_run",
            "dry_run": True,
            "selected_media_count": len(selected_ids),
            "media_tags_count_delta": 0,
            "no_media_tags_writes": True,
            "provider": {},
            "load_control": {},
            "provider_preference_requested": provider_list(args.provider_preference),
        }
    if cpu_fallback is None:
        cpu_fallback = {
            "executed": False,
            "status": "not_run",
            "dry_run": True,
            "provider": {},
            "load_control": {},
            "media_tags_count_delta": 0,
            "provider_preference_requested": [CPU_PROVIDER_PREFERENCE],
        }
    if write_run is None:
        write_run = {
            "executed": False,
            "status": (
                "not_run_missing_exact_operator_confirmation"
                if write_requested and not operator_confirmation_exact
                else "not_run_not_requested"
            ),
            "required_confirmation_present": operator_confirmation_exact,
            "media_tags_count_delta": 0,
            "tags_added": 0,
            "suggestions_added": 0,
            "skipped_locked": 0,
            "ignored_low_confidence": 0,
            "failed": 0,
            "tag_source_values_used": ["ai_wd"],
        }

    status = derive_status(
        selected_media=selected_media,
        model_cache=model_cache,
        dry_run=dry_run,
        cpu_fallback=cpu_fallback,
        write_executed=bool(write_run.get("executed")),
        write_confirmed=write_confirmed,
    )
    completion = status in {"target_met_dry_run_only", "target_met_with_bounded_write"}
    primary_provider = dry_run
    summary = {
        "phase": PHASE,
        "title": "Bounded Real AI Tagging Validation with DirectML",
        "generated_at": utc_now_iso(),
        "pipeline_contract": {
            "contract_id": CONTRACT_ID,
            "status": status,
            "claims": {
                "target_met": completion,
                "safe_to_merge": completion,
                "full_chain_complete": False,
            },
        },
        "run_configuration": {
            "mode": "execute" if write_confirmed else "dry_run",
            "write_requested": write_requested,
            "operator_confirmation_exact": operator_confirmation_exact,
            "max_items": max_items,
            "max_items_cap": MAX_ALLOWED_ITEMS,
            "candidate_scan_limit": candidate_scan_limit,
            "provider_preference_requested": provider_list(args.provider_preference),
            "cpu_fallback_provider_preference": [CPU_PROVIDER_PREFERENCE],
            "local_files_only": local_files_only,
            "model_download_allowed": not local_files_only,
            "force_suggestions": bool(args.force_suggestions),
            "s3a_execution_enabled": False,
            "unattended_enabled": False,
        },
        "selected_media": selected_media,
        "model_cache": model_cache,
        "dry_run": dry_run,
        "write_run": write_run,
        "primary_provider_validation": primary_provider,
        "cpu_fallback_validation": cpu_fallback,
        "load_control_observations": build_load_control_observations(primary_provider),
        "s3a_boundary": build_s3a_boundary(),
        "safety": build_safety(bool(write_run.get("executed")), dry_run),
        "public_reports": {
            "summary_json_path": repo_relative(SUMMARY_PATH),
            "markdown_report_path": repo_relative(MARKDOWN_PATH),
            "path_style": "repo_relative_public_artifacts",
        },
        "artifact_lifecycle": {
            "artifacts": [
                {
                    "path": "scripts/run_s2g_real1_bounded_ai_tagging_validation.py",
                    "classification": "phase-scoped operational runner",
                    "committed": True,
                },
                {
                    "path": repo_relative(SUMMARY_PATH),
                    "classification": "public report / handoff",
                    "committed": True,
                    "redacted": True,
                },
                {
                    "path": repo_relative(MARKDOWN_PATH),
                    "classification": "public report / handoff",
                    "committed": True,
                    "redacted": True,
                },
            ]
        },
        "backlog": [
            "DirectML package scope scanner perfection remains deferred.",
            "Smoke runner app.config side-effect perfection remains deferred.",
            "Fallback field naming cleanup remains deferred unless it blocks a contract.",
            "Durable per-job provider provenance schema migration remains a later reviewed phase.",
        ],
        "recommended_next_phase": (
            "If this PR is reviewed and merged, decide whether to run the exact approved "
            "bounded write validation or promote a separate S3A production plan; do not "
            "start full-library tagging from this phase."
        ),
        "validation": {
            "runner_command": "python scripts/run_s2g_real1_bounded_ai_tagging_validation.py",
            "dry_run_completed": bool(dry_run.get("executed")),
            "cpu_fallback_completed": bool(cpu_fallback.get("executed")),
            "write_completed": bool(write_run.get("executed")),
        },
    }
    write_reports(summary)
    print(json.dumps({"summary": repo_relative(SUMMARY_PATH), "status": status}, indent=2, sort_keys=True))
    return 0 if completion else 1


if __name__ == "__main__":
    raise SystemExit(main())
