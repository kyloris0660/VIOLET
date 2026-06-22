#!/usr/bin/env python3
"""Run the S3A-PROD1 operator-triggered incremental sync guard.

The runner accepts only explicit input paths, never falls back to full-library
scope, and performs production writes only when the exact S3A-PROD1
confirmation string is supplied. Without that confirmation it emits a
preflight/public report only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_s3a_pilot1_new_data_directml_chain as pilot  # noqa: E402

SUMMARY_PATH = ROOT / "docs" / "reports" / "s3a-prod1-operator-incremental-sync-summary.json"
MARKDOWN_PATH = ROOT / "docs" / "reports" / "s3a-prod1-operator-incremental-sync.md"

PHASE = "S3A-PROD1"
CONTRACT_ID = "s3a_prod1_operator_incremental_sync_contract_v1"
PRODUCTION_SYNC_CONFIRMATION = "I APPROVE S3A-PROD1 OPERATOR-TRIGGERED PRODUCTION SYNC"
MAX_ALLOWED_ITEMS = 5
DEFAULT_MAX_ITEMS = 5
DEFAULT_PROVIDER_PREFERENCE = "DmlExecutionProvider,CPUExecutionProvider"
CPU_PROVIDER_PREFERENCE = "CPUExecutionProvider"
SOURCE_LABEL = "violet:s3a-prod1:operator-incremental-sync-guard"

SelectedCandidate = pilot.SelectedCandidate
SUPPORTED_EXTENSIONS = pilot.SUPPORTED_EXTENSIONS


def provider_list(raw: str) -> list[str]:
    return pilot.provider_list(raw)


def provider_preference_is_bounded_directml_cpu(provider_preference: str) -> bool:
    return provider_list(provider_preference) == ["DmlExecutionProvider", "CPUExecutionProvider"]


def provider_preference_includes_directml(provider_preference: str) -> bool:
    return "DmlExecutionProvider" in provider_list(provider_preference)


def check_provider_availability(provider_preference: str) -> dict[str, Any]:
    requested = provider_list(provider_preference)
    result: dict[str, Any] = {
        "reported": True,
        "requested_provider_preference": requested,
        "available_providers": [],
        "directml_available": False,
        "cpu_fallback_available": False,
        "status": "not_checked",
        "error_type": None,
    }
    try:
        import onnxruntime as rt

        available = list(rt.get_available_providers())
        result.update(
            {
                "available_providers": available,
                "directml_available": "DmlExecutionProvider" in available,
                "cpu_fallback_available": "CPUExecutionProvider" in available,
                "status": "available" if available else "blocked_no_providers",
            }
        )
    except Exception as exc:  # noqa: BLE001
        result.update({"status": "blocked_provider_check_failed", "error_type": exc.__class__.__name__})
    return result


def safe_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
    except OSError:
        return False


def protected_input_roots() -> list[tuple[str, Path]]:
    from backend.app.config import settings

    roots: list[tuple[str, Path]] = [
        ("settings.STORAGE_ROOT", Path(settings.STORAGE_ROOT)),
        ("settings.MEDIA_DIR", Path(settings.MEDIA_DIR)),
        ("settings.ORIGINAL_DIR", Path(settings.ORIGINAL_DIR)),
        ("settings.THUMBNAIL_DIR", Path(settings.THUMBNAIL_DIR)),
        ("settings.CACHE_DIR", Path(settings.CACHE_DIR)),
        ("settings.DATA_DIR", Path(settings.DATA_DIR)),
        ("repo.data", ROOT / "data"),
        ("repo.media", ROOT / "media"),
    ]
    unique: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for label, root in roots:
        try:
            key = str(root.resolve()).casefold()
        except OSError:
            key = str(root).casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append((label, root))
    return unique


def protected_root_labels_for_path(path: Path, roots: list[tuple[str, Path]]) -> list[str]:
    return [label for label, root in roots if safe_is_relative_to(path, root)]


def source_safety_entry(path: Path, *, safe_label: str, protected_roots: list[tuple[str, Path]]) -> tuple[bool, dict[str, Any], int | None]:
    entry: dict[str, Any] = {
        "label": safe_label,
        "paths_redacted": True,
        "supported_extension": path.suffix.casefold() in SUPPORTED_EXTENSIONS,
        "protected_root_overlap": False,
        "protected_root_labels": [],
        "source_gate": None,
        "local_readable": False,
        "nonzero_size": False,
        "stable_enough": True,
        "blocked": False,
        "reason": "source_available",
    }
    if not entry["supported_extension"]:
        entry.update({"blocked": True, "reason": "unsupported_extension"})
        return False, entry, None

    protected_labels = protected_root_labels_for_path(path, protected_roots)
    if protected_labels:
        entry.update(
            {
                "protected_root_overlap": True,
                "protected_root_labels": protected_labels,
                "blocked": True,
                "reason": "protected_app_storage_input",
            }
        )
        return False, entry, None

    from backend.app.services.source_ingestion_gate import SourceIngestionGate

    gate = SourceIngestionGate.evaluate_path_source(path, safe_label=safe_label, hydration_policy_enabled=False)
    entry["source_gate"] = gate.to_public_dict()
    if entry["source_gate"].get("cloud_state") is not None:
        entry["cloud_detection_supported_platform"] = bool(entry["source_gate"]["cloud_state"].get("supported_platform"))
    if gate.blocked:
        entry.update({"blocked": True, "reason": gate.reason})
        return False, entry, None

    try:
        stat = path.stat()
    except OSError as exc:
        entry.update({"blocked": True, "reason": "stat_error", "error_type": exc.__class__.__name__})
        return False, entry, None

    size_bytes = int(stat.st_size)
    entry["local_readable"] = bool(os.access(path, os.R_OK))
    entry["nonzero_size"] = size_bytes > 0
    if not entry["local_readable"]:
        entry.update({"blocked": True, "reason": "source_not_readable"})
        return False, entry, size_bytes
    if size_bytes <= 0:
        entry.update({"blocked": True, "reason": "zero_byte_source"})
        return False, entry, size_bytes
    return True, entry, size_bytes


def discover_input_candidates(input_paths: list[str], *, max_items: int) -> tuple[list[SelectedCandidate], dict[str, Any]]:
    protected_roots = protected_input_roots()
    discovered: list[Path] = []
    missing_inputs = 0
    directory_inputs = 0
    file_inputs = 0
    unsupported_seen = 0
    path_entries: list[dict[str, Any]] = []

    for raw in input_paths:
        path = Path(raw).expanduser().resolve()
        protected_labels = protected_root_labels_for_path(path, protected_roots)
        if protected_labels:
            path_entries.append(
                {
                    "label": f"input_{len(path_entries) + 1:03d}",
                    "paths_redacted": True,
                    "blocked": True,
                    "reason": "protected_app_storage_input",
                    "protected_root_overlap": True,
                    "protected_root_labels": protected_labels,
                }
            )
            continue
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

    safe_paths: list[tuple[Path, int]] = []
    for index, path in enumerate(unique, start=1):
        safe_label = f"item_{index:03d}"
        allowed, entry, size_bytes = source_safety_entry(path, safe_label=safe_label, protected_roots=protected_roots)
        path_entries.append(entry)
        if allowed and size_bytes is not None:
            safe_paths.append((path, size_bytes))

    blocked_entries = [entry for entry in path_entries if entry.get("blocked")]
    over_cap_count = max(0, len(safe_paths) - max_items)
    selection_blocked = bool(blocked_entries or over_cap_count)
    selected_paths = [] if selection_blocked else safe_paths
    candidates = [
        SelectedCandidate(
            label=f"item_{index:03d}",
            path=path,
            suffix=path.suffix.casefold(),
            size_bytes=size_bytes,
        )
        for index, (path, size_bytes) in enumerate(selected_paths, start=1)
    ]
    protected_blocked = sum(1 for entry in blocked_entries if entry.get("reason") == "protected_app_storage_input")
    cloud_blocked = sum(1 for entry in blocked_entries if str(entry.get("reason", "")).startswith("cloud_"))
    platform_support_values = [
        bool(entry.get("cloud_detection_supported_platform"))
        for entry in path_entries
        if "cloud_detection_supported_platform" in entry
    ]
    platform_detection_supported = all(platform_support_values) if platform_support_values else sys.platform.startswith("win")
    source_safety_passed = not blocked_entries
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
        "protected_input_gate": {
            "reported": True,
            "passed": protected_blocked == 0,
            "blocked_count": protected_blocked,
            "protected_root_labels": [label for label, _root in protected_roots],
            "paths_redacted": True,
        },
        "source_safety_gate": {
            "reported": True,
            "passed": source_safety_passed,
            "blocked_count": len(blocked_entries),
            "cloud_placeholder_blocked_count": cloud_blocked,
            "protected_path_blocked_count": protected_blocked,
            "local_readable_files": sum(1 for entry in path_entries if entry.get("local_readable")),
            "nonzero_files": sum(1 for entry in path_entries if entry.get("nonzero_size")),
            "supported_extension_files": sum(1 for entry in path_entries if entry.get("supported_extension")),
            "read_probe_used": False,
            "hydration_policy_enabled": False,
            "cloud_detection": {
                "method": "SourceIngestionGate.evaluate_path_source metadata_only",
                "platform_specific_cloud_files_detection": platform_detection_supported,
                "conservative_fallback": not platform_detection_supported,
            },
            "stability_policy": {
                "enabled": False,
                "stable_enough": True,
                "reason": "no_s3a_prod1_min_age_policy_configured",
            },
            "public_item_results": path_entries,
        },
    }
    return candidates, scope


def empty_ai_tagging_result(
    *,
    label: str,
    status: str,
    dry_run: bool,
    provider_preference: str,
    local_files_only: bool,
    selected_media_count: int = 0,
) -> dict[str, Any]:
    return {
        "label": label,
        "reported": True,
        "executed": False,
        "status": status,
        "dry_run": dry_run,
        "local_files_only": local_files_only,
        "provider_preference_requested": provider_list(provider_preference),
        "selected_media_count": selected_media_count,
        "processed": 0,
        "tags_added": 0,
        "suggestions_added": 0,
        "skipped_locked": 0,
        "ignored_low_confidence": 0,
        "failed": 0,
        "rollback_error": False,
        "error_state": False,
        "media_tags_count_before": 0,
        "media_tags_count_after": 0,
        "media_tags_count_delta": 0,
        "media_with_ai_tags_before": 0,
        "media_with_ai_tags_after": 0,
        "media_with_ai_tags_delta": 0,
        "first_time_media_tag_insertion_proven": False,
        "no_media_tags_writes": True if dry_run else None,
        "tag_source_values_used": ["ai_wd"],
        "job_record_created": False,
        "provider": {},
        "load_control": {},
        "runtime_provenance": {},
        "public_item_results": [],
    }


def empty_classification_result(*, status: str) -> dict[str, Any]:
    return {
        "reported": True,
        "executed": False,
        "status": status,
        "classified_count": 0,
        "reused_classification_count": 0,
        "failed_count": 0,
        "content_class_distribution": {},
    }


def build_provider_write_gate(
    provider_availability: dict[str, Any],
    provider_preference: str,
    provider_probe: dict[str, Any],
    *,
    production_write_requested: bool,
    production_confirmed: bool,
) -> dict[str, Any]:
    requested = provider_list(provider_preference)
    preference_includes_directml = "DmlExecutionProvider" in requested
    preference_includes_cpu = "CPUExecutionProvider" in requested
    preference_dml_then_cpu = requested == ["DmlExecutionProvider", "CPUExecutionProvider"]
    directml_available = bool(provider_availability.get("directml_available"))
    cpu_available = bool(provider_availability.get("cpu_fallback_available"))
    probe_executed = bool(provider_probe.get("executed"))
    probe_status = provider_probe.get("status")
    probe_failed = int(provider_probe.get("failed", 0) or 0)
    probe_rollback_error = bool(provider_probe.get("rollback_error"))
    probe_error_state = bool(provider_probe.get("error_state"))
    probe_actual = provider_probe.get("provider", {}).get("actual_provider")
    blockers: list[str] = []

    if not production_write_requested:
        blockers.append("production_write_not_requested")
    if production_write_requested and not production_confirmed:
        blockers.append("exact_confirmation_missing")
    if production_confirmed and not preference_dml_then_cpu:
        if not preference_includes_directml:
            blockers.append("provider_preference_missing_directml")
        if not preference_includes_cpu:
            blockers.append("provider_preference_missing_cpu_fallback")
        if preference_includes_directml and preference_includes_cpu:
            blockers.append("provider_preference_not_dml_then_cpu")
    if production_confirmed and not directml_available:
        blockers.append("directml_unavailable")
    if production_confirmed and preference_dml_then_cpu and directml_available:
        if not probe_executed:
            blockers.append("directml_probe_not_executed")
        if probe_status != "completed":
            blockers.append("directml_probe_status_not_completed")
        if probe_failed:
            blockers.append("directml_probe_failed")
        if probe_rollback_error:
            blockers.append("directml_probe_rollback_error")
        if probe_error_state:
            blockers.append("directml_probe_error_state")
        if probe_actual != "DmlExecutionProvider":
            blockers.append("directml_probe_actual_provider_not_directml")

    passed = bool(production_confirmed and not blockers)
    return {
        "reported": True,
        "write_requested": bool(production_write_requested),
        "exact_confirmation_present": bool(production_confirmed),
        "requested_provider_preference": requested,
        "requires_directml_provider": True,
        "provider_preference_includes_directml": preference_includes_directml,
        "provider_preference_includes_cpu_fallback": preference_includes_cpu,
        "provider_preference_dml_then_cpu": preference_dml_then_cpu,
        "directml_available": directml_available,
        "cpu_fallback_available": cpu_available,
        "probe_executed": probe_executed,
        "probe_status": probe_status,
        "probe_failed": probe_failed,
        "probe_rollback_error": probe_rollback_error,
        "probe_error_state": probe_error_state,
        "probe_actual_provider": probe_actual,
        "passed": passed,
        "write_allowed": passed,
        "no_cpu_only_write_path": preference_dml_then_cpu and probe_actual != "CPUExecutionProvider",
        "blockers": blockers,
    }


def build_preflight(scope: dict[str, Any], model_cache: dict[str, Any], provider_availability: dict[str, Any]) -> dict[str, Any]:
    over_cap_count = int(scope.get("over_cap_count", 0) or 0)
    return {
        "reported": True,
        "input_mode": "input_path",
        "discovered_supported_files": int(scope.get("supported_files", 0) or 0),
        "selected_count": int(scope.get("selected_count", 0) or 0),
        "over_cap_check": {
            "passed": over_cap_count == 0,
            "over_cap_count": over_cap_count,
            "max_items": int(scope.get("max_items", 0) or 0),
        },
        "model_cache": {
            "status": model_cache.get("status"),
            "local_files_only": model_cache.get("local_files_only"),
            "model_file_cached": model_cache.get("model_file_cached"),
            "label_file_cached": model_cache.get("label_file_cached"),
            "model_download_allowed": model_cache.get("model_download_allowed"),
            "model_download_performed": model_cache.get("model_download_performed"),
        },
        "provider_availability": provider_availability,
        "directml_available": bool(provider_availability.get("directml_available")),
        "cpu_fallback_available": bool(provider_availability.get("cpu_fallback_available")),
        "protected_input_gate": scope.get("protected_input_gate"),
        "source_safety_gate": scope.get("source_safety_gate"),
        "no_full_library_fallback": True,
        "public_private_path_redaction": {
            "public_path_redaction": scope.get("public_path_redaction"),
            "absolute_paths_redacted": True,
            "file_labels_redacted": True,
            "private_locator_values_recorded": False,
        },
    }


def build_import_write_preconditions(
    scope: dict[str, Any],
    model_cache: dict[str, Any],
    provider_availability: dict[str, Any],
    provider_preference: str,
    *,
    production_write_requested: bool,
    production_confirmed: bool,
    local_files_only: bool,
) -> dict[str, Any]:
    max_items = int(scope.get("max_items", 0) or 0)
    selected_count = int(scope.get("selected_count", 0) or 0)
    over_cap_count = int(scope.get("over_cap_count", 0) or 0)
    checks = {
        "write_requested": bool(production_write_requested),
        "input_path_mode": True,
        "local_files_only": bool(local_files_only),
        "model_cache_cached": model_cache.get("status") == "cached",
        "model_download_not_allowed": not bool(model_cache.get("model_download_allowed")),
        "model_download_not_performed": not bool(model_cache.get("model_download_performed")),
        "provider_preference_dml_then_cpu": provider_preference_is_bounded_directml_cpu(provider_preference),
        "directml_available": bool(provider_availability.get("directml_available")),
        "cpu_fallback_available": bool(provider_availability.get("cpu_fallback_available")),
        "protected_input_gate_passed": bool(scope.get("protected_input_gate", {}).get("passed", False)),
        "source_safety_gate_passed": bool(scope.get("source_safety_gate", {}).get("passed", False)),
        "scope_valid": 1 <= selected_count <= max_items <= MAX_ALLOWED_ITEMS,
        "no_over_cap_input": over_cap_count == 0,
        "no_full_library_fallback": bool(scope.get("no_full_library_fallback")),
        "exact_confirmation_present": bool(production_confirmed),
    }
    required_keys = (
        "input_path_mode",
        "local_files_only",
        "model_cache_cached",
        "model_download_not_allowed",
        "model_download_not_performed",
        "provider_preference_dml_then_cpu",
        "directml_available",
        "cpu_fallback_available",
        "protected_input_gate_passed",
        "source_safety_gate_passed",
        "scope_valid",
        "no_over_cap_input",
        "no_full_library_fallback",
        "exact_confirmation_present",
    )
    blockers = [key for key in required_keys if not checks[key]]
    return {
        **checks,
        "passed": bool(production_write_requested) and bool(production_confirmed) and not blockers,
        "blockers": blockers,
    }


def classify_media_scope(db: Any, media_ids: list[int]) -> dict[str, Any]:
    return pilot.classify_media_scope(db, media_ids)


def validate_localization_reuse(db: Any, tag_names: list[str]) -> dict[str, Any]:
    result = pilot.validate_localization_reuse(db, tag_names)
    result["llm_external_provider_used"] = False
    result["external_provider_used"] = False
    result.setdefault("missing_or_deferred", 0)
    if result.get("deferred_reason") == "external_llm_provider_not_approved_for_s3a_pilot1":
        result["deferred_reason"] = "external_llm_provider_not_approved_for_s3a_prod1"
    result["candidate_tags_reported_as"] = "count_only_public_report"
    return result


def build_s3a_boundary(production_confirmed: bool, import_executed: bool, ai_write_executed: bool) -> dict[str, Any]:
    return {
        "operator_triggered_production_sync_enabled": bool(production_confirmed),
        "single_operator_triggered_run_only": True,
        "production_automation_enabled": False,
        "scheduled_automation_enabled": False,
        "unattended_s3b_enabled": False,
        "broad_production_sync_enabled": False,
        "full_library_fallback_enabled": False,
        "stages": [
            {"name": "preflight", "operator_write_executed": False},
            {"name": "import_reuse", "operator_write_executed": import_executed},
            {"name": "classification", "operator_write_executed": False},
            {"name": "directml_ai_tagging", "operator_write_executed": ai_write_executed},
            {"name": "localization", "operator_write_executed": False},
            {"name": "summary_ledger", "operator_write_executed": False},
        ],
    }


def load_control_observations(ai_run: dict[str, Any], fallback_run: dict[str, Any]) -> dict[str, Any]:
    observations = pilot.load_control_observations(ai_run, fallback_run)
    observations["single_operator_triggered_run_only"] = True
    return observations


def cpu_fallback_success(summary: dict[str, Any]) -> bool:
    return pilot.cpu_fallback_success(summary)


def stage_has_failures(stage: dict[str, Any]) -> bool:
    status = str(stage.get("status") or "").casefold()
    failed = int(stage.get("failed_count", stage.get("failed", 0)) or 0)
    return bool(
        failed
        or stage.get("error_state")
        or stage.get("rollback_error")
        or "with_item_failures" in status
        or status.startswith("failed")
    )


def has_media_tags_write_proof(ai: dict[str, Any]) -> bool:
    return int(ai.get("media_tags_count_delta", 0) or 0) > 0 or bool(ai.get("first_time_media_tag_insertion_proven"))


def production_write_completed(summary: dict[str, Any]) -> bool:
    ai = summary.get("directml_ai_tagging", {})
    import_reuse = summary.get("import_reuse", {})
    return bool(
        summary.get("pipeline_contract", {}).get("status") == "target_met_with_bounded_write"
        and int(import_reuse.get("failed_count", 0) or 0) == 0
        and int(ai.get("failed", 0) or 0) == 0
        and ai.get("provider", {}).get("actual_provider") == "DmlExecutionProvider"
        and has_media_tags_write_proof(ai)
    )


def derive_status(summary: dict[str, Any]) -> str:
    scope = summary.get("scope", {})
    config = summary.get("run_configuration", {})
    if int(scope.get("protected_input_gate", {}).get("blocked_count", 0) or 0):
        return "blocked_protected_input_path"
    if int(scope.get("source_safety_gate", {}).get("blocked_count", 0) or 0):
        return "blocked_source_safety_gate"
    if int(scope.get("over_cap_count", 0) or 0):
        return "blocked_input_over_cap"
    if int(scope.get("selected_count", 0) or 0) <= 0:
        return "blocked_scope_invalid"
    if summary.get("model_cache", {}).get("model_download_allowed"):
        return "blocked_model_download_allowed"
    if summary.get("model_cache", {}).get("status") != "cached":
        return "blocked_model_cache_missing"
    if not provider_preference_is_bounded_directml_cpu(",".join(config.get("provider_preference_requested", []) or [])):
        return "blocked_provider_preference_invalid"
    if not summary.get("preflight", {}).get("directml_available"):
        return "blocked_directml_unavailable"
    if not summary.get("preflight", {}).get("cpu_fallback_available"):
        return "blocked_cpu_fallback_unavailable"
    if summary.get("db_session", {}).get("available") is False:
        return "blocked_db_unavailable"

    production_write_requested = bool(config.get("production_write_requested"))
    production_confirmed = bool(config.get("exact_production_sync_confirmation"))
    if production_write_requested and not production_confirmed:
        return "blocked_production_write_requested_without_exact_confirmation"

    if stage_has_failures(summary.get("import_reuse", {})):
        return "blocked_import_item_failures"
    if stage_has_failures(summary.get("classification", {})):
        return "blocked_classification_failures"
    if stage_has_failures(summary.get("directml_ai_tagging", {})):
        return "blocked_ai_tagging_item_failures"
    if int(summary.get("localization", {}).get("failed", 0) or 0):
        return "blocked_localization_failures"

    if not production_confirmed:
        return "preflight_completed_write_confirmation_required"

    if not summary.get("import_write_preconditions", {}).get("passed"):
        return "blocked_import_write_prerequisites"
    if summary.get("import_reuse", {}).get("failed_count", 0):
        return "blocked_import_item_failures"
    if summary.get("import_reuse", {}).get("downstream_media_count", 0) <= 0:
        return "blocked_no_media"
    if summary.get("classification", {}).get("failed_count", 0):
        return "blocked_classification_failures"

    ai = summary.get("directml_ai_tagging", {})
    if not summary.get("provider_write_gate", {}).get("passed"):
        return "blocked_directml_provider_not_validated"
    if not ai.get("executed") or ai.get("dry_run"):
        return "blocked_ai_tagging_write_not_executed"
    if ai.get("provider", {}).get("actual_provider") != "DmlExecutionProvider":
        return "blocked_directml_provider_not_validated"
    if not has_media_tags_write_proof(ai):
        return "write_executed_but_first_time_insertion_unproven"
    if not cpu_fallback_success(summary):
        return "blocked_cpu_fallback_not_validated"
    return "target_met_with_bounded_write"


def apply_pipeline_status(summary: dict[str, Any], status: str) -> None:
    completion = status == "target_met_with_bounded_write"
    summary["pipeline_contract"] = {
        "contract_id": CONTRACT_ID,
        "status": status,
        "claims": {
            "target_met": completion,
            "safe_to_merge": completion,
            "full_chain_complete": completion,
        },
    }


def build_safety(summary: dict[str, Any]) -> dict[str, Any]:
    config = summary.get("run_configuration", {})
    import_reuse = summary.get("import_reuse", {})
    ai = summary.get("directml_ai_tagging", {})
    exact = bool(config.get("exact_production_sync_confirmation"))
    return {
        "max_items_lte_5": int(config.get("max_items", 0) or 0) <= MAX_ALLOWED_ITEMS,
        "explicit_input_required": config.get("input_mode") == "input_path",
        "selected_input_explicit_bounded": bool(summary.get("scope", {}).get("selected_count", 0))
        and bool(summary.get("scope", {}).get("no_full_library_fallback")),
        "no_full_library_run": True,
        "no_full_library_fallback": True,
        "single_operator_triggered_run_only": True,
        "production_write_without_confirmation": bool(import_reuse.get("executed") or (ai.get("executed") and not ai.get("dry_run"))) and not exact,
        "import_write_without_confirmation": bool(import_reuse.get("executed")) and not exact,
        "ai_tagging_write_without_confirmation": bool(ai.get("executed")) and not bool(ai.get("dry_run")) and not exact,
        "db_import": bool(import_reuse.get("imported_count", 0)),
        "media_tags_write_executed": bool(ai.get("executed")) and not bool(ai.get("dry_run")),
        "operator_triggered_production_sync_enabled": exact,
        "production_automation_enabled": False,
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
        "protected_app_storage_input": bool(summary.get("scope", {}).get("protected_input_gate", {}).get("blocked_count", 0)),
        "source_safety_gate_passed": bool(summary.get("scope", {}).get("source_safety_gate", {}).get("passed", False)),
        "cloud_hydration_or_recall_triggered": False,
        "model_download": bool(config.get("model_download_allowed", False)),
        "local_files_only": bool(config.get("local_files_only", False)),
        "public_redaction_passed": bool(summary.get("public_redaction", {}).get("passed", False)),
        "private_locator_values_recorded": False,
        "external_llm_provider_used": bool(summary.get("localization", {}).get("llm_external_provider_used", False)),
    }


def render_markdown(summary: dict[str, Any]) -> str:
    status = summary.get("pipeline_contract", {}).get("status")
    scope = summary.get("scope", {})
    preflight = summary.get("preflight", {})
    import_reuse = summary.get("import_reuse", {})
    classification = summary.get("classification", {})
    probe = summary.get("directml_provider_probe", {})
    gate = summary.get("provider_write_gate", {})
    ai = summary.get("directml_ai_tagging", {})
    cpu = summary.get("cpu_fallback_validation", {})
    loc = summary.get("localization", {})
    load = summary.get("load_control_observations", {})
    config = summary.get("run_configuration", {})

    lines = [
        "# S3A-PROD1: Operator-Triggered Production Incremental Sync Guard",
        "",
        f"Status: `{status}`.",
        "",
        f"Contract: `{CONTRACT_ID}`.",
        "",
        f"Public summary: `{pilot.repo_relative(SUMMARY_PATH)}`.",
        "",
        "## Scope",
        "",
        f"- Input mode: `{scope.get('input_mode')}`.",
        f"- Selected count: `{scope.get('selected_count')}`.",
        f"- Discovered supported files: `{scope.get('supported_files')}`.",
        f"- Over-cap count: `{scope.get('over_cap_count', 0)}`.",
        f"- Max items: `{config.get('max_items')}`.",
        f"- Full-library fallback: `{not scope.get('no_full_library_fallback', False)}`.",
        f"- Public path redaction: `{scope.get('public_path_redaction')}`.",
        f"- Protected input gate passed: `{scope.get('protected_input_gate', {}).get('passed')}`.",
        f"- Source safety gate passed: `{scope.get('source_safety_gate', {}).get('passed')}`.",
        f"- Source safety blockers: `{scope.get('source_safety_gate', {}).get('blocked_count')}`.",
        "",
        "## Preflight",
        "",
        f"- Model cache status: `{summary.get('model_cache', {}).get('status')}`.",
        f"- Model download performed: `{summary.get('model_cache', {}).get('model_download_performed')}`.",
        f"- Local files only: `{config.get('local_files_only')}`.",
        f"- DirectML available: `{preflight.get('directml_available')}`.",
        f"- CPU fallback available: `{preflight.get('cpu_fallback_available')}`.",
        f"- No full-library fallback: `{preflight.get('no_full_library_fallback')}`.",
        "",
        "## Import / Reuse",
        "",
        f"- Executed import write: `{import_reuse.get('executed')}`.",
        f"- Production confirmation present: `{config.get('exact_production_sync_confirmation')}`.",
        f"- Import write preconditions passed: `{summary.get('import_write_preconditions', {}).get('passed')}`.",
        f"- Import write blockers: `{summary.get('import_write_preconditions', {}).get('blockers')}`.",
        f"- Imported: `{import_reuse.get('imported_count')}`.",
        f"- Reused: `{import_reuse.get('reused_count')}`.",
        f"- Would import: `{import_reuse.get('would_import_count')}`.",
        f"- Skipped: `{import_reuse.get('skipped_count')}`.",
        f"- Failed: `{import_reuse.get('failed_count')}`.",
        f"- App-managed storage writes: `{import_reuse.get('app_managed_storage_writes')}`.",
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
        f"- DirectML prewrite probe status: `{probe.get('status')}`.",
        f"- DirectML prewrite probe provider: `{probe.get('provider', {}).get('actual_provider')}`.",
        f"- DirectML write gate passed: `{gate.get('passed')}`.",
        f"- DirectML write gate blockers: `{gate.get('blockers')}`.",
        f"- Executed: `{ai.get('executed')}`.",
        f"- Dry run: `{ai.get('dry_run')}`.",
        f"- Actual provider: `{ai.get('provider', {}).get('actual_provider')}`.",
        f"- Provider preference: `{ai.get('provider_preference_requested')}`.",
        f"- Processed: `{ai.get('processed')}`.",
        f"- Failed: `{ai.get('failed')}`.",
        f"- Tags added: `{ai.get('tags_added')}`.",
        f"- Suggestions added: `{ai.get('suggestions_added')}`.",
        f"- Skipped locked: `{ai.get('skipped_locked')}`.",
        f"- Ignored low confidence: `{ai.get('ignored_low_confidence')}`.",
        f"- Media tags before/after/delta: `{ai.get('media_tags_count_before')}` / `{ai.get('media_tags_count_after')}` / `{ai.get('media_tags_count_delta')}`.",
        f"- Media with AI tags delta: `{ai.get('media_with_ai_tags_delta')}`.",
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
        f"- Candidate tags: `{loc.get('candidate_tags_count')}`.",
        f"- Reused translations: `{loc.get('reused_translations')}`.",
        f"- New translations: `{loc.get('new_translations')}`.",
        f"- Missing/deferred: `{loc.get('missing_or_deferred')}`.",
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
        "- Single operator-triggered run only.",
        "- Production automation remains disabled.",
        "- Unattended S3B remains disabled.",
        "- Provider/Pixiv/gallery-dl/SauceNAO/Google, SourceConcept/R1/R2/R1R, and Entity bridge were not run.",
        "- Cleanup/delete/reset/drop/truncate was not run.",
        "- Public reports are aggregate and path-redacted.",
        "",
    ]
    return "\n".join(lines)


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
    if "validation" in summary:
        summary["validation"]["production_write_completed"] = production_write_completed(summary)
    summary["safety"] = build_safety(summary)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    MARKDOWN_PATH.write_text(render_markdown(summary), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run S3A-PROD1 operator-triggered production incremental sync guard.")
    parser.add_argument("--input-path", action="append", required=True, help="Explicit input file or directory. May be repeated.")
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    parser.add_argument("--provider-preference", default=DEFAULT_PROVIDER_PREFERENCE)
    parser.add_argument("--production-sync-confirmation", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    max_items = int(args.max_items)
    if not (1 <= max_items <= MAX_ALLOWED_ITEMS):
        raise SystemExit(f"--max-items must be between 1 and {MAX_ALLOWED_ITEMS}.")

    local_files_only = True
    confirmation_value = str(args.production_sync_confirmation or "")
    production_write_requested = bool(confirmation_value.strip())
    production_confirmed = confirmation_value == PRODUCTION_SYNC_CONFIRMATION

    candidates, scope = discover_input_candidates(args.input_path, max_items=max_items)
    started_at = pilot.utc_now_iso()
    model_cache: dict[str, Any] = {}
    provider_availability: dict[str, Any] = {}
    preflight: dict[str, Any] = {}
    import_write_preconditions: dict[str, Any] = {}
    import_reuse: dict[str, Any] = {"reported": False, "status": "not_run"}
    classification: dict[str, Any] = empty_classification_result(status="not_run")
    directml_ai_tagging: dict[str, Any] = empty_ai_tagging_result(
        label="directml_primary",
        status="not_run",
        dry_run=True,
        provider_preference=args.provider_preference,
        local_files_only=local_files_only,
    )
    directml_provider_probe: dict[str, Any] = empty_ai_tagging_result(
        label="directml_prewrite_probe",
        status="not_required_preflight_only",
        dry_run=True,
        provider_preference=args.provider_preference,
        local_files_only=local_files_only,
    )
    cpu_fallback: dict[str, Any] = empty_ai_tagging_result(
        label="cpu_fallback_dry_run",
        status="not_run",
        dry_run=True,
        provider_preference=CPU_PROVIDER_PREFERENCE,
        local_files_only=local_files_only,
    )
    provider_write_gate: dict[str, Any] = {}
    localization: dict[str, Any] = {"reported": False, "status": "not_run"}
    db_session: dict[str, Any] = {"reported": False, "available": False}
    downstream_media_ids: list[int] = []
    touched_tags: list[str] = []

    pilot.SOURCE_LABEL = SOURCE_LABEL
    with pilot.temporary_env(pilot.base_runtime_env(max_items, args.provider_preference)):
        model_cache = pilot.check_model_cache(local_files_only)
        provider_availability = check_provider_availability(args.provider_preference)
        provider_write_gate = build_provider_write_gate(
            provider_availability,
            args.provider_preference,
            directml_provider_probe,
            production_write_requested=production_write_requested,
            production_confirmed=production_confirmed,
        )
        preflight = build_preflight(scope, model_cache, provider_availability)
        import_write_preconditions = build_import_write_preconditions(
            scope,
            model_cache,
            provider_availability,
            args.provider_preference,
            production_write_requested=production_write_requested,
            production_confirmed=production_confirmed,
            local_files_only=local_files_only,
        )
        db = None
        try:
            db = pilot.get_db_session()
            db_session = {"reported": True, "available": True, "error_type": None}
        except Exception as exc:  # noqa: BLE001
            db_session = {"reported": True, "available": False, "error_type": exc.__class__.__name__}

        if db is None:
            selected_count = int(scope.get("selected_count", 0) or 0)
            import_reuse = {
                "reported": True,
                "input_mode": "input_path",
                "executed": False,
                "write_requested": production_write_requested,
                "exact_confirmation_present": production_confirmed,
                "write_preconditions_passed": False,
                "write_blockers": ["db_session_unavailable"],
                "status": "not_run_db_unavailable",
                "files_discovered": selected_count,
                "files_supported": selected_count,
                "imported_count": 0,
                "reused_count": 0,
                "would_import_count": 0,
                "skipped_count": selected_count,
                "failed_count": 0,
                "downstream_media_count": 0,
                "no_full_library_fallback": True,
                "source_icloud_mutation": False,
                "app_managed_storage_writes": 0,
                "public_item_results": [],
                "private_locator_values_recorded": False,
                "public_path_redaction": "paths_and_filenames_redacted",
                "created_files_public_count": 0,
            }
            classification = empty_classification_result(status="not_run_db_unavailable")
            directml_ai_tagging = empty_ai_tagging_result(
                label="directml_primary",
                status="not_run_db_unavailable",
                dry_run=True,
                provider_preference=args.provider_preference,
                local_files_only=local_files_only,
            )
            directml_provider_probe = empty_ai_tagging_result(
                label="directml_prewrite_probe",
                status="not_run_db_unavailable",
                dry_run=True,
                provider_preference=args.provider_preference,
                local_files_only=local_files_only,
            )
            cpu_fallback = empty_ai_tagging_result(
                label="cpu_fallback_dry_run",
                status="not_run_db_unavailable",
                dry_run=True,
                provider_preference=CPU_PROVIDER_PREFERENCE,
                local_files_only=local_files_only,
            )
            provider_write_gate = build_provider_write_gate(
                provider_availability,
                args.provider_preference,
                directml_provider_probe,
                production_write_requested=production_write_requested,
                production_confirmed=production_confirmed,
            )
            localization = {
                "reported": True,
                "attempted": False,
                "status": "not_run_db_unavailable",
                "candidate_tags_count": 0,
                "reused_translations": 0,
                "new_translations": 0,
                "missing_or_deferred": 0,
                "failed": 0,
                "llm_external_provider_used": False,
                "external_provider_used": False,
                "deferred_reason": "db_session_unavailable",
            }
        else:
            try:
                import_reuse, downstream_media_ids = pilot.import_or_reuse_from_input(
                    db,
                    candidates,
                    write_requested=production_write_requested,
                    execute_import=bool(production_confirmed and import_write_preconditions.get("passed")),
                    import_confirmed=production_confirmed,
                    write_preconditions_passed=bool(import_write_preconditions.get("passed")),
                    write_blockers=list(import_write_preconditions.get("blockers") or []),
                )

                downstream_allowed = bool(downstream_media_ids) and (
                    not production_confirmed or bool(import_write_preconditions.get("passed"))
                )
                if downstream_allowed:
                    classification = classify_media_scope(db, downstream_media_ids)
                else:
                    classification = empty_classification_result(
                        status="not_run_import_write_preconditions_blocked"
                        if production_confirmed and not import_write_preconditions.get("passed")
                        else "not_run_no_downstream_media"
                    )

                if model_cache.get("status") == "cached" and local_files_only and downstream_allowed:
                    if production_confirmed:
                        can_probe_directml = provider_preference_is_bounded_directml_cpu(args.provider_preference) and bool(
                            provider_availability.get("directml_available")
                        )
                        if can_probe_directml:
                            started = time.perf_counter()
                            directml_provider_probe, _probe_tags = pilot.run_ai_tagging_pass(
                                db,
                                label="directml_prewrite_probe",
                                media_ids=downstream_media_ids[:1],
                                dry_run=True,
                                provider_preference=args.provider_preference,
                                max_items=1,
                                local_files_only=local_files_only,
                            )
                            directml_provider_probe["operator_guard_elapsed_seconds"] = round(time.perf_counter() - started, 4)
                        else:
                            directml_provider_probe = empty_ai_tagging_result(
                                label="directml_prewrite_probe",
                                status="not_run_provider_write_gate_blocked",
                                dry_run=True,
                                provider_preference=args.provider_preference,
                                local_files_only=local_files_only,
                                selected_media_count=1,
                            )

                        provider_write_gate = build_provider_write_gate(
                            provider_availability,
                            args.provider_preference,
                            directml_provider_probe,
                            production_write_requested=production_write_requested,
                            production_confirmed=production_confirmed,
                        )
                        if provider_write_gate.get("passed"):
                            started = time.perf_counter()
                            directml_ai_tagging, touched_tags = pilot.run_ai_tagging_pass(
                                db,
                                label="directml_primary",
                                media_ids=downstream_media_ids,
                                dry_run=False,
                                provider_preference=args.provider_preference,
                                max_items=max_items,
                                local_files_only=local_files_only,
                            )
                            directml_ai_tagging["operator_guard_elapsed_seconds"] = round(time.perf_counter() - started, 4)
                        else:
                            directml_ai_tagging = empty_ai_tagging_result(
                                label="directml_primary",
                                status="not_run_provider_write_gate_blocked",
                                dry_run=True,
                                provider_preference=args.provider_preference,
                                local_files_only=local_files_only,
                                selected_media_count=len(downstream_media_ids),
                            )
                            directml_ai_tagging["write_gate_blockers"] = list(provider_write_gate.get("blockers") or [])
                    else:
                        started = time.perf_counter()
                        directml_ai_tagging, touched_tags = pilot.run_ai_tagging_pass(
                            db,
                            label="directml_primary",
                            media_ids=downstream_media_ids,
                            dry_run=True,
                            provider_preference=args.provider_preference,
                            max_items=max_items,
                            local_files_only=local_files_only,
                        )
                        directml_ai_tagging["operator_guard_elapsed_seconds"] = round(time.perf_counter() - started, 4)
                        provider_write_gate = build_provider_write_gate(
                            provider_availability,
                            args.provider_preference,
                            directml_provider_probe,
                            production_write_requested=production_write_requested,
                            production_confirmed=production_confirmed,
                        )

                    cpu_fallback, _cpu_tags = pilot.run_ai_tagging_pass(
                        db,
                        label="cpu_fallback_dry_run",
                        media_ids=downstream_media_ids[:1],
                        dry_run=True,
                        provider_preference=CPU_PROVIDER_PREFERENCE,
                        max_items=1,
                        local_files_only=local_files_only,
                    )
                else:
                    directml_ai_tagging = empty_ai_tagging_result(
                        label="directml_primary",
                        status="not_run_pending_import_or_model_cache",
                        dry_run=True,
                        provider_preference=args.provider_preference,
                        local_files_only=local_files_only,
                    )
                    directml_provider_probe = empty_ai_tagging_result(
                        label="directml_prewrite_probe",
                        status="not_run_pending_import_or_model_cache",
                        dry_run=True,
                        provider_preference=args.provider_preference,
                        local_files_only=local_files_only,
                    )
                    cpu_fallback = empty_ai_tagging_result(
                        label="cpu_fallback_dry_run",
                        status="not_run_pending_import_or_model_cache",
                        dry_run=True,
                        provider_preference=CPU_PROVIDER_PREFERENCE,
                        local_files_only=local_files_only,
                    )
                    provider_write_gate = build_provider_write_gate(
                        provider_availability,
                        args.provider_preference,
                        directml_provider_probe,
                        production_write_requested=production_write_requested,
                        production_confirmed=production_confirmed,
                    )

                localization = validate_localization_reuse(db, touched_tags)
            finally:
                db.close()

    summary: dict[str, Any] = {
        "phase": PHASE,
        "title": "Operator-Triggered Production Incremental Sync Guard",
        "generated_at": pilot.utc_now_iso(),
        "started_at": started_at,
        "pipeline_contract": {
            "contract_id": CONTRACT_ID,
            "status": "pending",
            "claims": {"target_met": False, "safe_to_merge": False, "full_chain_complete": False},
        },
        "run_configuration": {
            "mode": "production_write" if production_confirmed else ("blocked_wrong_confirmation" if production_write_requested else "preflight"),
            "input_mode": "input_path",
            "max_items": max_items,
            "max_items_cap": MAX_ALLOWED_ITEMS,
            "provider_preference_requested": provider_list(args.provider_preference),
            "cpu_fallback_provider_preference": [CPU_PROVIDER_PREFERENCE],
            "local_files_only": local_files_only,
            "model_download_allowed": False,
            "production_write_requested": production_write_requested,
            "exact_production_sync_confirmation": production_confirmed,
            "import_write_requested": production_write_requested,
            "import_confirmation_exact": production_confirmed,
            "ai_tagging_write_requested": production_write_requested,
            "ai_tagging_confirmation_exact": production_confirmed,
            "single_operator_triggered_run_only": True,
            "no_full_library_fallback": True,
            "production_automation_enabled": False,
            "scheduled_automation_enabled": False,
            "unattended_s3b_enabled": False,
        },
        "scope": scope,
        "preflight": preflight,
        "model_cache": model_cache,
        "provider_availability": provider_availability,
        "db_session": db_session,
        "import_write_preconditions": import_write_preconditions,
        "import_reuse": import_reuse,
        "classification": classification,
        "directml_provider_probe": directml_provider_probe,
        "provider_write_gate": provider_write_gate,
        "directml_ai_tagging": directml_ai_tagging,
        "primary_provider_validation": directml_ai_tagging,
        "cpu_fallback_validation": cpu_fallback,
        "localization": localization,
        "load_control_observations": load_control_observations(directml_ai_tagging, cpu_fallback),
        "s3a_boundary": build_s3a_boundary(
            production_confirmed,
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
            "unattended_s3b": False,
        },
        "public_reports": {
            "summary_json_path": pilot.repo_relative(SUMMARY_PATH),
            "markdown_report_path": pilot.repo_relative(MARKDOWN_PATH),
            "path_style": "repo_relative_public_artifacts",
        },
        "private_ledger": {
            "written": False,
            "committed": False,
            "reason": "public aggregate report sufficient for S3A-PROD1 validation",
        },
        "artifact_lifecycle": {
            "artifacts": [
                {
                    "path": "scripts/run_s3a_prod1_operator_incremental_sync_guard.py",
                    "classification": "phase-scoped operational runner",
                    "committed": True,
                },
                {
                    "path": pilot.repo_relative(SUMMARY_PATH),
                    "classification": "public report / handoff",
                    "committed": True,
                    "redacted": True,
                },
                {
                    "path": pilot.repo_relative(MARKDOWN_PATH),
                    "classification": "public report / handoff",
                    "committed": True,
                    "redacted": True,
                },
            ]
        },
        "validation": {
            "runner_command": "python scripts/run_s3a_prod1_operator_incremental_sync_guard.py",
            "preflight_completed": True,
            "production_write_completed": False,
        },
        "backlog": [
            "Unattended S3B automation remains deferred.",
            "Scheduled production sync remains disabled.",
            "Full-library fallback remains forbidden for S3A-PROD1.",
        ],
    }
    apply_pipeline_status(summary, derive_status(summary))
    summary["validation"]["production_write_completed"] = production_write_completed(summary)
    summary["safety"] = build_safety(summary)
    write_reports(summary)
    final_status = str(summary.get("pipeline_contract", {}).get("status") or "")
    success_statuses = {"preflight_completed_write_confirmation_required", "target_met_with_bounded_write"}
    return 0 if final_status in success_statuses else 2


if __name__ == "__main__":
    raise SystemExit(main())
