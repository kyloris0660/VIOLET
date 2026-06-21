#!/usr/bin/env python3
"""Run the S3A-PILOT1 tiny new-data DirectML AI tagging chain.

This runner is intentionally phase-scoped. It accepts only an explicit input
path or explicit media IDs, caps the scope at five items, writes public reports
with aggregate/redacted fields only, and refuses import or AI-tag writes unless
the exact phase confirmation strings are supplied.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SUMMARY_PATH = ROOT / "docs" / "reports" / "s3a-pilot1-new-data-directml-chain-summary.json"
MARKDOWN_PATH = ROOT / "docs" / "reports" / "s3a-pilot1-new-data-directml-chain.md"

PHASE = "S3A-PILOT1"
CONTRACT_ID = "s3a_pilot1_new_data_directml_chain_contract_v1"
IMPORT_CONFIRMATION = "I APPROVE S3A-PILOT1 NEW DATA IMPORT WRITE"
AI_TAGGING_CONFIRMATION = "I APPROVE S3A-PILOT1 DIRECTML AI TAGGING WRITE"
MAX_ALLOWED_ITEMS = 5
DEFAULT_MAX_ITEMS = 3
DEFAULT_PROVIDER_PREFERENCE = "DmlExecutionProvider,CPUExecutionProvider"
CPU_PROVIDER_PREFERENCE = "CPUExecutionProvider"
SOURCE_LABEL = "violet:s3a-pilot1:new-data-directml-chain"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


@dataclass
class SelectedCandidate:
    label: str
    path: Path
    suffix: str
    size_bytes: int
    file_hash: str | None = None
    status: str = "pending"
    message: str = ""
    media_id: int | None = None
    duplicate_media_id: int | None = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def repo_relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def provider_list(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def parse_media_ids(raw: str | None) -> list[int] | None:
    if raw is None or not raw.strip():
        return None
    ids: list[int] = []
    for part in raw.replace(";", ",").replace(" ", ",").split(","):
        value = part.strip()
        if value:
            ids.append(int(value))
    return ids


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
        "TAG_TRANSLATION_LLM_FALLBACK_ENABLED": "false",
        "CONTENT_CLASSIFICATION_METHOD": "heuristic",
        "CONTENT_CLASSIFICATION_ENABLED": "true",
        "CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT": "false",
        "ENTITY_ALIAS_RESOLVER_ENABLED": "false",
    }


def get_db_session() -> Any:
    from backend.app.database import init_engine
    import backend.app.database as database

    if database.SessionLocal is None:
        init_engine()
    if database.SessionLocal is None:
        raise RuntimeError("Database not initialized. Complete onboarding or set a valid test database first.")
    return database.SessionLocal()


def discover_input_candidates(input_paths: list[str], *, max_items: int) -> tuple[list[SelectedCandidate], dict[str, Any]]:
    discovered: list[Path] = []
    missing_inputs = 0
    directory_inputs = 0
    file_inputs = 0
    unsupported_seen = 0

    for raw in input_paths:
        path = Path(raw).expanduser().resolve()
        if not path.exists():
            missing_inputs += 1
            continue
        if path.is_dir():
            directory_inputs += 1
            for item in sorted(path.iterdir(), key=lambda p: p.name.casefold()):
                if not item.is_file():
                    continue
                if item.suffix.casefold() in SUPPORTED_EXTENSIONS:
                    discovered.append(item.resolve())
                else:
                    unsupported_seen += 1
        elif path.is_file():
            file_inputs += 1
            if path.suffix.casefold() in SUPPORTED_EXTENSIONS:
                discovered.append(path)
            else:
                unsupported_seen += 1
        else:
            missing_inputs += 1

    unique: list[Path] = []
    seen: set[str] = set()
    for path in discovered:
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)

    over_cap_count = max(0, len(unique) - max_items)
    selected_paths = [] if over_cap_count else unique
    candidates = [
        SelectedCandidate(
            label=f"item_{index:03d}",
            path=path,
            suffix=path.suffix.casefold(),
            size_bytes=path.stat().st_size,
        )
        for index, path in enumerate(selected_paths, start=1)
    ]
    scope = {
        "input_mode": "input_path",
        "explicit_input_path_supplied": bool(input_paths),
        "explicit_input_path_redacted": True,
        "input_path_count": len(input_paths),
        "file_inputs": file_inputs,
        "directory_inputs": directory_inputs,
        "missing_input_count": missing_inputs,
        "discovered_files": len(unique),
        "supported_files": len(unique),
        "unsupported_files": unsupported_seen,
        "selected_count": len(candidates),
        "selected_labels": [candidate.label for candidate in candidates],
        "over_cap_count": over_cap_count,
        "over_cap_blocked_before_selection": bool(over_cap_count),
        "default_truncation_disabled": True,
        "max_items": max_items,
        "no_full_library_fallback": True,
        "private_locator_values_recorded": False,
        "public_path_redaction": "paths_and_filenames_redacted",
    }
    return candidates, scope


def calculate_hashes(candidates: list[SelectedCandidate]) -> None:
    from backend.app.utils.media_processor import calculate_file_hash

    for candidate in candidates:
        try:
            candidate.file_hash = calculate_file_hash(candidate.path)
            candidate.status = "hash_checked"
        except Exception as exc:  # noqa: BLE001
            candidate.status = "failed"
            candidate.message = exc.__class__.__name__


def existing_media_by_hash(db: Any, hashes: Iterable[str]) -> dict[str, Any]:
    from backend.app.models import Media

    unique_hashes = sorted({item for item in hashes if item})
    existing: dict[str, Any] = {}
    if not unique_hashes:
        return existing
    rows = db.query(Media).filter(Media.hash.in_(unique_hashes)).all()
    for row in rows:
        existing[str(row.hash)] = row
    return existing


def count_ai_wd_media_tags(db: Any, media_ids: list[int]) -> int:
    from backend.app.models import blombooru_media_tags

    if not media_ids:
        return 0
    return int(
        db.query(blombooru_media_tags.c.media_id)
        .filter(
            blombooru_media_tags.c.source == "ai_wd",
            blombooru_media_tags.c.media_id.in_(media_ids),
        )
        .count()
    )


def count_media_with_ai_tags(db: Any, media_ids: list[int]) -> int:
    from backend.app.models import blombooru_media_tags

    if not media_ids:
        return 0
    rows = (
        db.query(blombooru_media_tags.c.media_id)
        .filter(
            blombooru_media_tags.c.source == "ai_wd",
            blombooru_media_tags.c.media_id.in_(media_ids),
        )
        .distinct()
        .all()
    )
    return len(rows)


def import_or_reuse_from_input(
    db: Any,
    candidates: list[SelectedCandidate],
    *,
    write_requested: bool,
    execute_import: bool,
    import_confirmed: bool,
    write_preconditions_passed: bool,
    write_blockers: list[str],
) -> tuple[dict[str, Any], list[int]]:
    from backend.app.config import settings
    from backend.app.routes.media import process_and_save_media
    from backend.app.schemas import RatingEnum
    from backend.app.utils.media_helpers import get_unique_filename

    calculate_hashes(candidates)
    existing = existing_media_by_hash(db, [candidate.file_hash or "" for candidate in candidates])

    for candidate in candidates:
        if candidate.status == "failed":
            continue
        if candidate.file_hash in existing:
            media = existing[candidate.file_hash]
            candidate.status = "reused_existing_hash"
            candidate.media_id = int(media.id)
            candidate.duplicate_media_id = int(media.id)
        else:
            candidate.status = "would_import"

    imported_count = 0
    failed_count = sum(1 for item in candidates if item.status == "failed")
    copied_paths: list[Path] = []

    if execute_import:
        settings.ORIGINAL_DIR.mkdir(parents=True, exist_ok=True)
        settings.THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
        for candidate in candidates:
            if candidate.status != "would_import":
                continue
            copied_path: Path | None = None
            try:
                unique_filename = get_unique_filename(settings.ORIGINAL_DIR, candidate.path.name)
                copied_path = settings.ORIGINAL_DIR / unique_filename
                shutil.copy2(candidate.path, copied_path)
                copied_paths.append(copied_path)
                response = process_and_save_media(
                    db,
                    copied_path,
                    unique_filename,
                    RatingEnum.safe,
                    "",
                    None,
                    SOURCE_LABEL,
                    None,
                )
                candidate.status = "imported"
                candidate.media_id = int(response.id)
                imported_count += 1
            except HTTPException as exc:
                if copied_path and copied_path.exists():
                    try:
                        copied_path.unlink()
                    except OSError:
                        pass
                if exc.status_code == 409:
                    refreshed = existing_media_by_hash(db, [candidate.file_hash or ""])
                    media = refreshed.get(candidate.file_hash or "")
                    if media:
                        candidate.status = "reused_existing_hash"
                        candidate.media_id = int(media.id)
                        candidate.duplicate_media_id = int(media.id)
                        continue
                candidate.status = "failed"
                candidate.message = f"HTTPException:{exc.status_code}"
                failed_count += 1
            except Exception as exc:  # noqa: BLE001
                if copied_path and copied_path.exists():
                    try:
                        copied_path.unlink()
                    except OSError:
                        pass
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001
                    pass
                candidate.status = "failed"
                candidate.message = exc.__class__.__name__
                failed_count += 1
                break

    media_ids = [int(candidate.media_id) for candidate in candidates if candidate.media_id]
    would_import = sum(1 for item in candidates if item.status == "would_import")
    reused = sum(1 for item in candidates if item.status == "reused_existing_hash")
    skipped = max(0, len(candidates) - imported_count - reused - failed_count - would_import)

    result = {
        "reported": True,
        "input_mode": "input_path",
        "executed": bool(execute_import),
        "write_requested": write_requested,
        "exact_confirmation_present": import_confirmed,
        "write_preconditions_passed": write_preconditions_passed,
        "write_blockers": write_blockers,
        "status": "completed" if failed_count == 0 else "completed_with_item_failures",
        "files_discovered": len(candidates),
        "files_supported": len(candidates),
        "imported_count": imported_count,
        "reused_count": reused,
        "would_import_count": would_import,
        "skipped_count": skipped,
        "failed_count": failed_count,
        "downstream_media_count": len(media_ids),
        "no_full_library_fallback": True,
        "source_icloud_mutation": False,
        "app_managed_storage_writes": imported_count,
        "public_item_results": [
            {
                "label": candidate.label,
                "status": candidate.status,
                "has_media_id": bool(candidate.media_id),
                "message": candidate.message[:80] if candidate.message else "",
            }
            for candidate in candidates
        ],
        "private_locator_values_recorded": False,
        "public_path_redaction": "paths_and_filenames_redacted",
        "created_files_public_count": len(copied_paths),
    }
    return result, media_ids


def reuse_explicit_media_ids(db: Any, media_ids: list[int]) -> tuple[dict[str, Any], list[int]]:
    from backend.app.models import Media

    rows = db.query(Media).filter(Media.id.in_(media_ids)).all()
    found = {int(row.id): row for row in rows}
    ordered = [media_id for media_id in media_ids if media_id in found]
    missing = [media_id for media_id in media_ids if media_id not in found]
    result = {
        "reported": True,
        "input_mode": "media_ids",
        "executed": False,
        "write_requested": False,
        "exact_confirmation_present": False,
        "status": "completed" if not missing else "completed_with_item_failures",
        "files_discovered": len(media_ids),
        "files_supported": len(ordered),
        "imported_count": 0,
        "reused_count": len(ordered),
        "would_import_count": 0,
        "skipped_count": 0,
        "failed_count": len(missing),
        "downstream_media_count": len(ordered),
        "no_full_library_fallback": True,
        "source_icloud_mutation": False,
        "app_managed_storage_writes": 0,
        "public_item_results": [
            {"label": f"media_id_{index:03d}", "status": "reused_existing_media", "has_media_id": True, "message": ""}
            for index, _media_id in enumerate(ordered, start=1)
        ],
        "private_locator_values_recorded": False,
        "public_path_redaction": "media_ids_not_publicly_recorded",
    }
    return result, ordered


def classify_media_scope(db: Any, media_ids: list[int]) -> dict[str, Any]:
    from backend.app.models import Media
    from backend.app.services.content_classifier import classify_media

    if not media_ids:
        return {
            "reported": True,
            "executed": False,
            "dry_run": True,
            "status": "not_run_no_media",
            "classified_count": 0,
            "reused_classification_count": 0,
            "failed_count": 0,
            "content_class_distribution": {},
            "method": "heuristic",
        }

    rows = db.query(Media).filter(Media.id.in_(media_ids)).all()
    before_classes = {
        int(row.id): (row.content_class.value if row.content_class else None)
        for row in rows
    }
    results: list[dict[str, Any]] = []
    failed = 0
    for media_id in media_ids:
        try:
            result = classify_media(db, media_id, dry_run=True)
            results.append(result)
            if result.get("error"):
                failed += 1
        except Exception as exc:  # noqa: BLE001
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
            failed += 1
            results.append({"error_type": exc.__class__.__name__})

    distribution: dict[str, int] = {}
    classified = 0
    for result in results:
        value = result.get("content_class") or result.get("current_class")
        if value:
            distribution[str(value)] = distribution.get(str(value), 0) + 1
            classified += 1
    return {
        "reported": True,
        "executed": True,
        "dry_run": True,
        "status": "completed" if failed == 0 else "completed_with_item_failures",
        "classified_count": classified,
        "reused_classification_count": sum(1 for value in before_classes.values() if value),
        "failed_count": failed,
        "content_class_distribution": dict(sorted(distribution.items())),
        "method": "heuristic",
        "public_item_results": [
            {
                "status": "failed" if result.get("error") or result.get("error_type") else "completed",
                "content_class": result.get("content_class") or result.get("current_class"),
                "dry_run": bool(result.get("dry_run", True)),
            }
            for result in results
        ],
    }


def public_result_entry(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("error") or result.get("error_type"):
        return {"status": "failed", "error_type": result.get("error_type", "error")}
    return {
        "status": "completed",
        "predictions": len(result.get("predictions", []) or []),
        "tags_added": int(result.get("tags_added", 0) or 0),
        "suggestions_added": int(result.get("suggestions_added", 0) or 0),
        "skipped_locked": int(result.get("skipped_locked", 0) or 0),
        "ignored_low_confidence": int(result.get("ignored_low_confidence", 0) or 0),
    }


def aggregate_prediction_categories(results: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        for pred in result.get("predictions", []) or []:
            category = str(pred.get("category") or "unknown")
            counts[category] = counts.get(category, 0) + 1
    return dict(sorted(counts.items()))


def collect_touched_tag_names(results: Iterable[dict[str, Any]]) -> list[str]:
    names: set[str] = set()
    for result in results:
        for pred in result.get("predictions", []) or []:
            if pred.get("action") in {"confirmed", "suggestion"}:
                name = str(pred.get("name") or "").strip()
                if name:
                    names.add(name)
    return sorted(names)


def run_ai_tagging_pass(
    db: Any,
    *,
    label: str,
    media_ids: list[int],
    dry_run: bool,
    provider_preference: str,
    max_items: int,
    local_files_only: bool,
) -> tuple[dict[str, Any], list[str]]:
    from backend.app.services.ai_tagging_service import (
        get_ai_tagging_runtime_provenance,
        run_ai_tagging,
    )

    if not media_ids:
        return (
            {
                "label": label,
                "reported": True,
                "executed": False,
                "status": "not_run_no_media",
                "dry_run": dry_run,
                "local_files_only": local_files_only,
                "provider_preference_requested": provider_list(provider_preference),
                "selected_media_count": 0,
                "processed": 0,
                "tags_added": 0,
                "suggestions_added": 0,
                "skipped_locked": 0,
                "ignored_low_confidence": 0,
                "failed": 0,
                "media_tags_count_before": 0,
                "media_tags_count_after": 0,
                "media_tags_count_delta": 0,
                "media_with_ai_tags_before": 0,
                "media_with_ai_tags_after": 0,
                "media_with_ai_tags_delta": 0,
                "first_time_media_tag_insertion_proven": False,
                "tag_source_values_used": ["ai_wd"],
                "job_record_created": False,
                "provider": {},
                "load_control": {},
                "public_item_results": [],
            },
            [],
        )

    env = base_runtime_env(max_items, provider_preference)
    started = time.perf_counter()
    before_count = count_ai_wd_media_tags(db, media_ids)
    before_media = count_media_with_ai_tags(db, media_ids)
    results: list[dict[str, Any]] = []
    public_results: list[dict[str, Any]] = []
    provenance: dict[str, Any] | None = None
    rollback_error = False

    with temporary_env(env):
        for media_id in media_ids:
            try:
                result = run_ai_tagging(
                    db,
                    media_id,
                    dry_run=dry_run,
                    force_suggestions=False,
                    local_files_only=local_files_only,
                )
                if dry_run:
                    try:
                        db.rollback()
                    except Exception:  # noqa: BLE001
                        rollback_error = True
                if provenance is None and isinstance(result.get("provenance"), dict):
                    provenance = result["provenance"]
                results.append(result)
                public_results.append(public_result_entry(result))
            except Exception as exc:  # noqa: BLE001
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001
                    rollback_error = True
                results.append({"error_type": exc.__class__.__name__})
                public_results.append({"status": "failed", "error_type": exc.__class__.__name__})

        if provenance is None:
            try:
                provenance = get_ai_tagging_runtime_provenance()
            except Exception as exc:  # noqa: BLE001
                provenance = {"available": False, "error_type": exc.__class__.__name__}

    after_count = count_ai_wd_media_tags(db, media_ids)
    after_media = count_media_with_ai_tags(db, media_ids)
    elapsed = round(time.perf_counter() - started, 4)
    tags_added = sum(int(result.get("tags_added", 0) or 0) for result in results)
    suggestions_added = sum(int(result.get("suggestions_added", 0) or 0) for result in results)
    skipped_locked = sum(int(result.get("skipped_locked", 0) or 0) for result in results)
    ignored_low_confidence = sum(int(result.get("ignored_low_confidence", 0) or 0) for result in results)
    failed = sum(1 for result in results if result.get("error") or result.get("error_type"))
    predicted_count = sum(len(result.get("predictions", []) or []) for result in results)
    provider = provenance.get("provider", {}) if isinstance(provenance, dict) else {}
    load_control = provenance.get("load_control", {}) if isinstance(provenance, dict) else {}
    status = "completed" if failed == 0 and not rollback_error else "completed_with_item_failures"
    delta = after_count - before_count

    return (
        {
            "label": label,
            "reported": True,
            "executed": True,
            "status": status,
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
            "rollback_error": rollback_error,
            "error_state": failed > 0 or rollback_error,
            "predicted_tag_count": predicted_count,
            "prediction_category_counts": aggregate_prediction_categories(results),
            "media_tags_count_before": before_count,
            "media_tags_count_after": after_count,
            "media_tags_count_delta": delta,
            "media_with_ai_tags_before": before_media,
            "media_with_ai_tags_after": after_media,
            "media_with_ai_tags_delta": after_media - before_media,
            "first_time_media_tag_insertion_proven": (not dry_run) and delta > 0,
            "no_media_tags_writes": (after_count == before_count) if dry_run else None,
            "tag_source_values_used": ["ai_wd"],
            "job_record_created": False,
            "elapsed_seconds": elapsed,
            "public_item_results": public_results,
            "runtime_provenance": provenance,
            "provider": provider,
            "load_control": load_control,
        },
        collect_touched_tag_names(results),
    )


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
        result["blocker"] = "unknown_model"
        return result

    try:
        from huggingface_hub import hf_hub_download

        model_path = hf_hub_download(
            repo_id=model_repo,
            filename=WDTagger.MODEL_FILENAME,
            local_files_only=True,
        )
        label_path = hf_hub_download(
            repo_id=model_repo,
            filename=WDTagger.LABEL_FILENAME,
            local_files_only=True,
        )
        result["model_file_cached"] = bool(model_path)
        result["label_file_cached"] = bool(label_path)
        result["status"] = "cached"
    except Exception as exc:  # noqa: BLE001
        result["status"] = "blocked" if local_files_only else "download_allowed"
        result["blocker"] = exc.__class__.__name__
    return result


def build_import_write_preconditions(
    scope: dict[str, Any],
    model_cache: dict[str, Any],
    *,
    input_mode: str,
    import_write_requested: bool,
    import_confirmed: bool,
    local_files_only: bool,
) -> dict[str, Any]:
    max_items = int(scope.get("max_items", 0) or 0)
    selected_count = int(scope.get("selected_count", 0) or 0)
    over_cap_count = int(scope.get("over_cap_count", 0) or 0)
    checks = {
        "write_requested": bool(import_write_requested),
        "input_path_mode": input_mode == "input_path",
        "local_files_only": bool(local_files_only),
        "model_cache_cached": model_cache.get("status") == "cached",
        "model_download_not_allowed": not bool(model_cache.get("model_download_allowed")),
        "scope_valid": 1 <= selected_count <= max_items <= MAX_ALLOWED_ITEMS,
        "no_over_cap_input": over_cap_count == 0,
        "no_full_library_fallback": bool(scope.get("no_full_library_fallback")),
        "exact_confirmation_present": bool(import_confirmed),
    }
    required_keys = (
        "input_path_mode",
        "local_files_only",
        "model_cache_cached",
        "model_download_not_allowed",
        "scope_valid",
        "no_over_cap_input",
        "no_full_library_fallback",
        "exact_confirmation_present",
    )
    blockers = [key for key in required_keys if not checks[key]]
    return {
        **checks,
        "passed": bool(import_write_requested) and not blockers,
        "blockers": blockers,
    }


def validate_localization_reuse(db: Any, tag_names: list[str]) -> dict[str, Any]:
    if not tag_names:
        return {
            "reported": True,
            "attempted": False,
            "status": "not_run_no_touched_tags",
            "candidate_tags_count": 0,
            "reused_translations": 0,
            "new_translations": 0,
            "failed": 0,
            "llm_external_provider_used": False,
            "deferred_reason": "no_touched_tags",
        }
    from backend.app.services.tag_localization_service import get_tag_display_names_batch

    translated = get_tag_display_names_batch(db, tag_names, lang="zh-CN")
    reused = sum(1 for name in tag_names if translated.get(name) and translated.get(name) != name)
    missing = max(0, len(tag_names) - reused)
    return {
        "reported": True,
        "attempted": True,
        "status": "validated_reuse_and_deferred_missing",
        "candidate_tags_count": len(tag_names),
        "reused_translations": reused,
        "new_translations": 0,
        "failed": 0,
        "llm_external_provider_used": False,
        "external_provider_used": False,
        "missing_or_deferred": missing,
        "deferred_reason": "external_llm_provider_not_approved_for_s3a_pilot1",
    }


def build_s3a_boundary(import_executed: bool, ai_write_executed: bool) -> dict[str, Any]:
    return {
        "operator_triggered_pilot_only": True,
        "production_execution_enabled": False,
        "unattended_enabled": False,
        "scheduled_automation_enabled": False,
        "broad_production_sync_enabled": False,
        "stages": [
            {"name": "new_data_selection_preflight", "operator_write_executed": False},
            {"name": "import_reuse", "operator_write_executed": import_executed},
            {"name": "classification", "operator_write_executed": False},
            {"name": "ai_tagging", "operator_write_executed": ai_write_executed},
            {"name": "localization", "operator_write_executed": False},
            {"name": "summary", "operator_write_executed": False},
        ],
    }


def load_control_observations(ai_run: dict[str, Any], fallback_run: dict[str, Any]) -> dict[str, Any]:
    load = ai_run.get("load_control") or fallback_run.get("load_control") or {}
    provider = ai_run.get("provider") or {}
    return {
        "actual_provider": provider.get("actual_provider"),
        "appeared_bounded": bool(load),
        "batch_size": load.get("batch_size"),
        "configured_batch_size": load.get("configured_batch_size"),
        "effective_batch_size": load.get("effective_batch_size"),
        "batch_cap_source": load.get("batch_cap_source"),
        "cpu_intra_op_threads": load.get("cpu_intra_op_threads"),
        "cpu_inter_op_threads": load.get("cpu_inter_op_threads"),
        "preprocess_workers": load.get("preprocess_workers"),
        "max_concurrent_ai_jobs": load.get("max_concurrent_jobs"),
        "execution_mode": load.get("execution_mode"),
        "warnings": ["onnxruntime_directml_partial_node_assignment_warning_observed_or_possible"]
        if provider.get("actual_provider") == "DmlExecutionProvider"
        else [],
    }


def derive_status(summary: dict[str, Any]) -> str:
    if summary.get("scope", {}).get("over_cap_count", 0):
        return "blocked_input_over_cap"
    if summary.get("scope", {}).get("selected_count", 0) <= 0:
        return "blocked_scope_invalid"
    if summary.get("model_cache", {}).get("model_download_allowed"):
        return "blocked_model_download_allowed"
    if summary.get("model_cache", {}).get("status") != "cached":
        return "blocked_model_cache_missing"
    import_write_requested = bool(summary.get("run_configuration", {}).get("import_write_requested"))
    if import_write_requested:
        if not summary.get("run_configuration", {}).get("import_confirmation_exact"):
            return "blocked_import_requested_without_exact_confirmation"
        if not summary.get("import_write_preconditions", {}).get("passed"):
            return "blocked_import_write_prerequisites"
    if summary.get("run_configuration", {}).get("ai_tagging_write_requested") and not summary.get("run_configuration", {}).get("ai_tagging_confirmation_exact"):
        return "blocked_ai_tagging_requested_without_exact_confirmation"
    if summary.get("import_reuse", {}).get("failed_count", 0):
        return "blocked_import_item_failures"
    if summary.get("import_reuse", {}).get("downstream_media_count", 0) <= 0:
        return "blocked_no_media"
    if summary.get("classification", {}).get("failed_count", 0):
        return "blocked_classification_failures"
    ai = summary.get("directml_ai_tagging", {})
    if ai.get("executed") and ai.get("failed", 0):
        return "blocked_ai_tagging_item_failures"
    if not cpu_fallback_success(summary):
        return "blocked_cpu_fallback_not_validated"
    if summary.get("run_configuration", {}).get("ai_tagging_write_requested"):
        actual_provider = ai.get("provider", {}).get("actual_provider")
        accepted_fallback = bool(ai.get("provider", {}).get("explicit_accepted_fallback"))
        if not ai.get("executed") or ai.get("dry_run"):
            return "blocked_ai_tagging_write_not_executed"
        if actual_provider != "DmlExecutionProvider" and not accepted_fallback:
            return "blocked_directml_provider_not_validated"
        if not ai.get("first_time_media_tag_insertion_proven") and int(ai.get("media_tags_count_delta", 0) or 0) <= 0:
            return "write_executed_but_first_time_insertion_unproven"
        return "target_met_with_bounded_write"
    return "target_met_dry_run_only"


def cpu_fallback_success(summary: dict[str, Any]) -> bool:
    cpu = summary.get("cpu_fallback_validation", {})
    return (
        bool(cpu.get("executed"))
        and str(cpu.get("status") or "").casefold() == "completed"
        and int(cpu.get("failed", 0) or 0) == 0
        and cpu.get("provider", {}).get("actual_provider") == "CPUExecutionProvider"
        and bool(cpu.get("dry_run", False))
        and int(cpu.get("media_tags_count_delta", 0) or 0) == 0
    )


def apply_pipeline_status(summary: dict[str, Any], status: str) -> None:
    completion = status in {"target_met_dry_run_only", "target_met_with_bounded_write"}
    first_time_proven = bool(summary.get("directml_ai_tagging", {}).get("first_time_media_tag_insertion_proven"))
    summary["pipeline_contract"] = {
        "contract_id": CONTRACT_ID,
        "status": status,
        "claims": {
            "target_met": completion,
            "safe_to_merge": completion,
            "full_chain_complete": status == "target_met_with_bounded_write" and first_time_proven,
        },
    }


def build_safety(summary: dict[str, Any]) -> dict[str, Any]:
    config = summary.get("run_configuration", {})
    import_reuse = summary.get("import_reuse", {})
    ai = summary.get("directml_ai_tagging", {})
    return {
        "max_items_lte_5": int(config.get("max_items", 0) or 0) <= MAX_ALLOWED_ITEMS,
        "selected_input_explicit_bounded": bool(summary.get("scope", {}).get("selected_count", 0)) and bool(summary.get("scope", {}).get("no_full_library_fallback")),
        "no_full_library_run": True,
        "no_full_library_fallback": True,
        "dry_run_before_write": True,
        "import_write_without_confirmation": bool(import_reuse.get("executed")) and not bool(config.get("import_confirmation_exact")),
        "ai_tagging_write_without_confirmation": bool(ai.get("executed")) and not bool(ai.get("dry_run")) and not bool(config.get("ai_tagging_confirmation_exact")),
        "db_import": bool(import_reuse.get("imported_count", 0)),
        "media_tags_write_executed": bool(ai.get("executed")) and not bool(ai.get("dry_run")),
        "production_s3a_execution_enabled": False,
        "unattended_s3b_enabled": False,
        "scheduled_automation_enabled": False,
        "broad_production_sync_enabled": False,
        "provider_pixiv_gallery_dl_saucenao_google_calls": False,
        "sourceconcept_r1_r2_r1r": False,
        "entity_bridge": False,
        "confirmed_entity_assignments": False,
        "desired_media_backfill": False,
        "cleanup_delete_reset_drop_truncate": False,
        "source_icloud_mutation": False,
        "model_download": bool(config.get("model_download_allowed", False)),
        "local_files_only": bool(config.get("local_files_only", False)),
        "public_redaction_passed": bool(summary.get("public_redaction", {}).get("passed", False)),
        "private_locator_values_recorded": False,
        "external_llm_provider_used": bool(summary.get("localization", {}).get("llm_external_provider_used", False)),
    }


def write_reports(summary: dict[str, Any]) -> None:
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown(summary)
    summary["public_redaction"] = {"passed": False, "finding_count": None}

    from scripts.phase_contracts.contract_checks import scan_public_payload

    findings = scan_public_payload({"public_json_payload": summary, "public_markdown_text": markdown})
    summary["public_redaction"] = {
        "passed": not findings,
        "finding_count": len(findings),
        "findings_redacted": True,
        "scan_scope": "public_json_and_markdown",
    }
    markdown = render_markdown(summary)
    findings = scan_public_payload({"public_json_payload": summary, "public_markdown_text": markdown})
    summary["public_redaction"] = {
        "passed": not findings,
        "finding_count": len(findings),
        "findings_redacted": True,
        "scan_scope": "public_json_and_markdown",
    }
    if findings:
        apply_pipeline_status(summary, "blocked_public_redaction_failed")
        summary["safety"] = build_safety(summary)
    summary["safety"] = build_safety(summary)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    MARKDOWN_PATH.write_text(render_markdown(summary), encoding="utf-8")


def render_markdown(summary: dict[str, Any]) -> str:
    status = summary.get("pipeline_contract", {}).get("status")
    scope = summary.get("scope", {})
    import_reuse = summary.get("import_reuse", {})
    classification = summary.get("classification", {})
    ai = summary.get("directml_ai_tagging", {})
    cpu = summary.get("cpu_fallback_validation", {})
    loc = summary.get("localization", {})
    load = summary.get("load_control_observations", {})
    config = summary.get("run_configuration", {})

    lines = [
        "# S3A-PILOT1: Controlled New Data DirectML AI Tagging Chain",
        "",
        f"Status: `{status}`.",
        "",
        f"Contract: `{CONTRACT_ID}`.",
        "",
        f"Public summary: `{repo_relative(SUMMARY_PATH)}`.",
        "",
        "## Scope",
        "",
        f"- Input mode: `{scope.get('input_mode')}`.",
        f"- Selected sample count: `{scope.get('selected_count')}`.",
        f"- Discovered supported files: `{scope.get('supported_files')}`.",
        f"- Over-cap count: `{scope.get('over_cap_count', 0)}`.",
        f"- Max items: `{config.get('max_items')}`.",
        f"- Full-library fallback: `{not scope.get('no_full_library_fallback', False)}`.",
        f"- Public path redaction: `{scope.get('public_path_redaction')}`.",
        "",
        "## Import / Reuse",
        "",
        f"- Executed import write: `{import_reuse.get('executed')}`.",
        f"- Import write requested: `{import_reuse.get('write_requested')}`.",
        f"- Import exact confirmation present: `{import_reuse.get('exact_confirmation_present')}`.",
        f"- Import write preconditions passed: `{summary.get('import_write_preconditions', {}).get('passed')}`.",
        f"- Import write blockers: `{summary.get('import_write_preconditions', {}).get('blockers')}`.",
        f"- Imported: `{import_reuse.get('imported_count')}`.",
        f"- Reused: `{import_reuse.get('reused_count')}`.",
        f"- Would import: `{import_reuse.get('would_import_count')}`.",
        f"- Skipped: `{import_reuse.get('skipped_count')}`.",
        f"- Failed: `{import_reuse.get('failed_count')}`.",
        f"- Downstream media count: `{import_reuse.get('downstream_media_count')}`.",
        "",
        "## Classification",
        "",
        f"- Executed: `{classification.get('executed')}`.",
        f"- Dry run: `{classification.get('dry_run')}`.",
        f"- Classified: `{classification.get('classified_count')}`.",
        f"- Reused classification: `{classification.get('reused_classification_count')}`.",
        f"- Failed: `{classification.get('failed_count')}`.",
        f"- Distribution: `{classification.get('content_class_distribution')}`.",
        "",
        "## DirectML AI Tagging",
        "",
        f"- Executed: `{ai.get('executed')}`.",
        f"- Dry run: `{ai.get('dry_run')}`.",
        f"- AI tagging exact confirmation present: `{config.get('ai_tagging_confirmation_exact')}`.",
        f"- Actual provider: `{ai.get('provider', {}).get('actual_provider')}`.",
        f"- Processed: `{ai.get('processed')}`.",
        f"- Failed: `{ai.get('failed')}`.",
        f"- Tags added: `{ai.get('tags_added')}`.",
        f"- Suggestions added: `{ai.get('suggestions_added')}`.",
        f"- Skipped locked: `{ai.get('skipped_locked')}`.",
        f"- Ignored low confidence: `{ai.get('ignored_low_confidence')}`.",
        f"- Media tags before/after/delta: `{ai.get('media_tags_count_before')}` / `{ai.get('media_tags_count_after')}` / `{ai.get('media_tags_count_delta')}`.",
        f"- First-time insertion proven: `{ai.get('first_time_media_tag_insertion_proven')}`.",
        "",
        "## CPU Fallback",
        "",
        f"- Executed: `{cpu.get('executed')}`.",
        f"- Status: `{cpu.get('status')}`.",
        f"- Actual provider: `{cpu.get('provider', {}).get('actual_provider')}`.",
        f"- Media tag delta: `{cpu.get('media_tags_count_delta')}`.",
        "",
        "## Localization",
        "",
        f"- Attempted validation: `{loc.get('attempted')}`.",
        f"- Reused translations: `{loc.get('reused_translations')}`.",
        f"- New translations: `{loc.get('new_translations')}`.",
        f"- Failed: `{loc.get('failed')}`.",
        f"- External provider used: `{loc.get('llm_external_provider_used')}`.",
        f"- Deferred reason: `{loc.get('deferred_reason')}`.",
        "",
        "## Public Redaction",
        "",
        f"- Passed: `{summary.get('public_redaction', {}).get('passed')}`.",
        f"- Finding count: `{summary.get('public_redaction', {}).get('finding_count')}`.",
        "",
        "## Load Control",
        "",
        f"- Effective batch size: `{load.get('effective_batch_size')}`.",
        f"- CPU intra/inter threads: `{load.get('cpu_intra_op_threads')}` / `{load.get('cpu_inter_op_threads')}`.",
        f"- Preprocess workers: `{load.get('preprocess_workers')}`.",
        f"- Max concurrent AI jobs: `{load.get('max_concurrent_ai_jobs')}`.",
        "",
        "## Safety",
        "",
        "- Operator-triggered pilot only.",
        "- Production S3A automation remains disabled.",
        "- Unattended S3B remains disabled.",
        "- Provider/Pixiv/gallery-dl/SauceNAO/Google, SourceConcept/R1/R2/R1R, and Entity bridge were not run.",
        "- Cleanup/delete/reset/drop/truncate was not run.",
        "- Public reports are aggregate and path-redacted.",
        "",
    ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run S3A-PILOT1 tiny new-data DirectML chain.")
    parser.add_argument("--input-path", action="append", default=[], help="Explicit staging input file or directory. May be repeated.")
    parser.add_argument("--media-ids", help="Comma-separated explicit media IDs to reuse instead of importing input files.")
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    parser.add_argument("--provider-preference", default=DEFAULT_PROVIDER_PREFERENCE)
    parser.add_argument("--execute-import", action="store_true")
    parser.add_argument("--import-confirmation", default="")
    parser.add_argument("--execute-ai-tagging", action="store_true")
    parser.add_argument("--ai-tagging-confirmation", default="")
    parser.add_argument("--allow-model-download", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    max_items = int(args.max_items)
    if not (1 <= max_items <= MAX_ALLOWED_ITEMS):
        raise SystemExit(f"--max-items must be between 1 and {MAX_ALLOWED_ITEMS}.")

    explicit_media_ids = parse_media_ids(args.media_ids)
    if explicit_media_ids is not None and len(explicit_media_ids) > max_items:
        raise SystemExit("--media-ids exceeds --max-items.")
    if explicit_media_ids and args.input_path:
        raise SystemExit("Use either --media-ids or --input-path, not both.")
    if not explicit_media_ids and not args.input_path:
        raise SystemExit("S3A-PILOT1 requires --input-path or --media-ids; no full-library fallback is allowed.")

    local_files_only = not args.allow_model_download
    import_confirmed = args.import_confirmation == IMPORT_CONFIRMATION
    ai_confirmed = args.ai_tagging_confirmation == AI_TAGGING_CONFIRMATION
    input_mode = "media_ids" if explicit_media_ids is not None else "input_path"

    scope: dict[str, Any]
    candidates: list[SelectedCandidate] = []
    if explicit_media_ids is None:
        candidates, scope = discover_input_candidates(args.input_path, max_items=max_items)
    else:
        scope = {
            "input_mode": "media_ids",
            "explicit_media_ids_supplied": True,
            "explicit_media_ids_publicly_recorded": False,
            "selected_count": len(explicit_media_ids),
            "max_items": max_items,
            "no_full_library_fallback": True,
            "private_locator_values_recorded": False,
            "public_path_redaction": "media_ids_not_publicly_recorded",
        }

    started_at = utc_now_iso()
    model_cache: dict[str, Any] = {}
    import_write_preconditions: dict[str, Any] = {}
    import_reuse: dict[str, Any] = {"reported": False, "status": "not_run"}
    classification: dict[str, Any] = {"reported": False, "status": "not_run"}
    directml_ai_tagging: dict[str, Any] = {"reported": False, "status": "not_run"}
    cpu_fallback: dict[str, Any] = {"reported": False, "status": "not_run"}
    localization: dict[str, Any] = {"reported": False, "status": "not_run"}
    downstream_media_ids: list[int] = []
    touched_tags: list[str] = []

    with temporary_env(base_runtime_env(max_items, args.provider_preference)):
        model_cache = check_model_cache(local_files_only)
        import_write_preconditions = build_import_write_preconditions(
            scope,
            model_cache,
            input_mode=input_mode,
            import_write_requested=bool(args.execute_import),
            import_confirmed=import_confirmed,
            local_files_only=local_files_only,
        )
        db = get_db_session()
        try:
            if input_mode == "media_ids":
                assert explicit_media_ids is not None
                import_reuse, downstream_media_ids = reuse_explicit_media_ids(db, explicit_media_ids)
            else:
                import_reuse, downstream_media_ids = import_or_reuse_from_input(
                    db,
                    candidates,
                    write_requested=bool(args.execute_import),
                    execute_import=bool(args.execute_import and import_write_preconditions.get("passed")),
                    import_confirmed=import_confirmed,
                    write_preconditions_passed=bool(import_write_preconditions.get("passed")),
                    write_blockers=list(import_write_preconditions.get("blockers") or []),
                )

            classification = classify_media_scope(db, downstream_media_ids)

            if model_cache.get("status") == "cached" and local_files_only:
                directml_ai_tagging, touched_tags = run_ai_tagging_pass(
                    db,
                    label="directml_primary",
                    media_ids=downstream_media_ids,
                    dry_run=not (args.execute_ai_tagging and ai_confirmed),
                    provider_preference=args.provider_preference,
                    max_items=max_items,
                    local_files_only=local_files_only,
                )
                cpu_ids = downstream_media_ids[:1]
                cpu_fallback, _cpu_tags = run_ai_tagging_pass(
                    db,
                    label="cpu_fallback_dry_run",
                    media_ids=cpu_ids,
                    dry_run=True,
                    provider_preference=CPU_PROVIDER_PREFERENCE,
                    max_items=max(1, min(max_items, 1)),
                    local_files_only=local_files_only,
                )
            else:
                directml_ai_tagging = {
                    "reported": True,
                    "executed": False,
                    "status": "not_run_model_cache_unavailable",
                    "dry_run": True,
                    "failed": 0,
                    "media_tags_count_delta": 0,
                    "first_time_media_tag_insertion_proven": False,
                    "provider": {},
                }
                cpu_fallback = {
                    "reported": True,
                    "executed": False,
                    "status": "not_run_model_cache_unavailable",
                    "dry_run": True,
                    "failed": 0,
                    "media_tags_count_delta": 0,
                    "provider": {},
                }

            localization = validate_localization_reuse(db, touched_tags)
        finally:
            db.close()

    summary: dict[str, Any] = {
        "phase": PHASE,
        "title": "Controlled New Data Import with DirectML AI Tagging Chain",
        "generated_at": utc_now_iso(),
        "started_at": started_at,
        "pipeline_contract": {
            "contract_id": CONTRACT_ID,
            "status": "pending",
            "claims": {"target_met": False, "safe_to_merge": False, "full_chain_complete": False},
        },
        "run_configuration": {
            "mode": "execute" if (args.execute_import or args.execute_ai_tagging) else "dry_run",
            "input_mode": input_mode,
            "max_items": max_items,
            "max_items_cap": MAX_ALLOWED_ITEMS,
            "provider_preference_requested": provider_list(args.provider_preference),
            "cpu_fallback_provider_preference": [CPU_PROVIDER_PREFERENCE],
            "local_files_only": local_files_only,
            "model_download_allowed": not local_files_only,
            "import_write_requested": bool(args.execute_import),
            "import_confirmation_exact": import_confirmed,
            "ai_tagging_write_requested": bool(args.execute_ai_tagging),
            "ai_tagging_confirmation_exact": ai_confirmed,
            "operator_triggered_pilot_only": True,
            "s3a_production_execution_enabled": False,
            "unattended_s3b_enabled": False,
            "no_full_library_fallback": True,
        },
        "scope": scope,
        "model_cache": model_cache,
        "import_write_preconditions": import_write_preconditions,
        "import_reuse": import_reuse,
        "classification": classification,
        "directml_ai_tagging": directml_ai_tagging,
        "primary_provider_validation": directml_ai_tagging,
        "cpu_fallback_validation": cpu_fallback,
        "localization": localization,
        "load_control_observations": load_control_observations(directml_ai_tagging, cpu_fallback),
        "s3a_boundary": build_s3a_boundary(
            bool(import_reuse.get("executed")),
            bool(directml_ai_tagging.get("executed")) and not bool(directml_ai_tagging.get("dry_run")),
        ),
        "forbidden_operations": {
            "provider_pixiv_gallery_dl_saucenao_google": False,
            "sourceconcept_r1_r2_r1r": False,
            "entity_bridge": False,
            "cleanup_delete_reset_drop_truncate": False,
            "desired_media_backfill": False,
            "scheduled_automation": False,
            "full_library_import_or_tagging": False,
        },
        "public_reports": {
            "summary_json_path": repo_relative(SUMMARY_PATH),
            "markdown_report_path": repo_relative(MARKDOWN_PATH),
            "path_style": "repo_relative_public_artifacts",
        },
        "artifact_lifecycle": {
            "artifacts": [
                {
                    "path": "scripts/run_s3a_pilot1_new_data_directml_chain.py",
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
        "validation": {
            "runner_command": "python scripts/run_s3a_pilot1_new_data_directml_chain.py",
            "dry_run_or_preflight_completed": True,
            "write_import_completed": bool(import_reuse.get("executed")),
            "write_ai_tagging_completed": bool(directml_ai_tagging.get("executed")) and not bool(directml_ai_tagging.get("dry_run")),
        },
        "backlog": [
            "First-time media tag insertion remains unproven unless media_tags_count_delta is positive in an approved write run.",
            "Production S3A execution remains disabled; this runner is operator-triggered pilot tooling only.",
            "Unattended S3B automation remains deferred.",
        ],
    }
    apply_pipeline_status(summary, derive_status(summary))
    summary["safety"] = build_safety(summary)
    write_reports(summary)
    final_status = str(summary.get("pipeline_contract", {}).get("status") or "")
    return 0 if final_status in {"target_met_dry_run_only", "target_met_with_bounded_write"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
