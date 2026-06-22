#!/usr/bin/env python3
"""Run S3A-PROD2/S3B-D1 bounded operator sync scale-up.

This phase-scoped runner accepts only explicit local input paths, caps the
batch at 20 items, requires one exact confirmation string for writes, keeps
model loading local-files-only by default, and records the disabled S3B
unattended/scheduled scaffold without starting any scheduler.
"""

from __future__ import annotations

import argparse
import atexit
import contextlib
import importlib.util
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PILOT_PATH = ROOT / "scripts" / "run_s3a_pilot1_new_data_directml_chain.py"
_pilot_spec = importlib.util.spec_from_file_location("s3a_pilot1_runner_for_prod2", PILOT_PATH)
assert _pilot_spec and _pilot_spec.loader
pilot = importlib.util.module_from_spec(_pilot_spec)
sys.modules.setdefault("s3a_pilot1_runner_for_prod2", pilot)
_pilot_spec.loader.exec_module(pilot)

from backend.app.services.s3b_unattended_sync_policy import (  # noqa: E402
    SourceFileDecision,
    SyncPolicy,
    build_disabled_scaffold,
    evaluate_source_file,
    public_file_decision_counts,
)


SUMMARY_PATH = ROOT / "docs" / "reports" / "s3a-prod2-s3b-d1-operator-scaleup-disabled-sync-summary.json"
MARKDOWN_PATH = ROOT / "docs" / "reports" / "s3a-prod2-s3b-d1-operator-scaleup-disabled-sync.md"
LOCK_PATH = ROOT / ".local_manifests" / "s3a_prod2_operator_sync" / "active.lock"

PHASE = "S3A-PROD2/S3B-D1"
CONTRACT_ID = "s3a_prod2_s3b_d1_operator_scaleup_disabled_sync_contract_v1"
WRITE_CONFIRMATION = "I APPROVE S3A-PROD2 BOUNDED OPERATOR SYNC"
DEFAULT_MAX_ITEMS = 5
MAX_ALLOWED_ITEMS = 20
DEFAULT_PROVIDER_PREFERENCE = "DmlExecutionProvider,CPUExecutionProvider"
DIRECTML_WRITE_PROVIDER_PREFERENCE = "DmlExecutionProvider"
CPU_PROVIDER_PREFERENCE = "CPUExecutionProvider"
SOURCE_LABEL = "violet:s3a-prod2:bounded-operator-sync-scaleup"
SUPPORTED_EXTENSIONS = pilot.SUPPORTED_EXTENSIONS
TARGET_STATUSES = {"target_met_dry_run_only", "target_met_with_bounded_write"}


@dataclass
class PreflightResult:
    candidates: list[Any]
    scope: dict[str, Any]
    source_file_preflight: dict[str, Any]


@dataclass
class OperatorSyncLock:
    path: Path = LOCK_PATH
    acquired: bool = False
    acquire_error: str | None = None

    def acquire(self) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "phase": PHASE,
            "pid": os.getpid(),
            "started_at": utc_now_iso(),
            "lock_scope": "s3a_prod2_operator_sync_write_window",
        }
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            self.acquired = False
            self.acquire_error = "active_operator_sync_lock_exists"
            return self.public_state(acquisition_attempted=True)
        except OSError as exc:
            self.acquired = False
            self.acquire_error = exc.__class__.__name__
            return self.public_state(acquisition_attempted=True)

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True)
                handle.write("\n")
        except OSError as exc:
            self.acquired = False
            self.acquire_error = exc.__class__.__name__
            with contextlib.suppress(OSError):
                self.path.unlink()
            return self.public_state(acquisition_attempted=True)

        self.acquired = True
        self.acquire_error = None
        atexit.register(self.release)
        return self.public_state(acquisition_attempted=True)

    def release(self) -> None:
        if not self.acquired:
            return
        with contextlib.suppress(OSError):
            self.path.unlink()
        self.acquired = False

    def public_state(self, *, acquisition_attempted: bool = False) -> dict[str, Any]:
        return {
            "reported": True,
            "acquisition_attempted": acquisition_attempted,
            "acquired": self.acquired,
            "durable_lock_held": self.acquired,
            "lock_scope": "s3a_prod2_operator_sync_write_window",
            "lock_path": repo_relative(self.path),
            "lock_path_redacted": True,
            "held_through_report_finalization": self.acquired,
            "error": self.acquire_error,
        }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def repo_relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def provider_list(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


@contextlib.contextmanager
def prod2_runtime_env(max_items: int, provider_preference: str) -> Iterator[None]:
    updates = pilot.base_runtime_env(max_items, provider_preference)
    updates.update(
        {
            "AI_TAGGING_BATCH_MAX_ITEMS": str(max_items),
            "AI_TAGGING_PROVIDER_PREFERENCE": provider_preference,
            "AI_TAGGING_AUTO_LOCALIZATION": "false",
            "TAG_TRANSLATION_AUTO_ENABLED": "false",
            "TAG_TRANSLATION_BACKGROUND_ENABLED": "false",
            "TAG_TRANSLATION_LLM_ENABLED": "false",
            "TAG_TRANSLATION_LLM_FALLBACK_ENABLED": "false",
            "ENTITY_ALIAS_RESOLVER_ENABLED": "false",
        }
    )
    with pilot.temporary_env(updates):
        yield


def provider_preference_is_bounded_directml_cpu(raw: str) -> bool:
    return provider_list(raw) == ["DmlExecutionProvider", "CPUExecutionProvider"]


def safe_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
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


def split_protected_input_paths(input_paths: list[str]) -> tuple[list[str], dict[str, Any]]:
    roots = protected_input_roots()
    allowed: list[str] = []
    blocked_entries: list[dict[str, Any]] = []
    for index, raw in enumerate(input_paths, start=1):
        path = Path(raw).expanduser()
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path.absolute()
        labels = protected_root_labels_for_path(resolved, roots)
        if labels:
            blocked_entries.append(
                {
                    "label": f"input_{index:03d}",
                    "blocked": True,
                    "reason": "protected_app_storage_input",
                    "protected_root_overlap": True,
                    "protected_root_labels": labels,
                    "paths_redacted": True,
                }
            )
            continue
        allowed.append(raw)

    blocked_count = len(blocked_entries)
    return allowed, {
        "reported": True,
        "passed": blocked_count == 0,
        "blocked_count": blocked_count,
        "protected_root_labels": [label for label, _root in roots],
        "public_item_results": blocked_entries,
        "paths_redacted": True,
    }


def _protected_input_decision(safe_label: str, labels: list[str]) -> SourceFileDecision:
    return SourceFileDecision(
        safe_label=safe_label,
        eligible=False,
        reason="protected_app_storage_input",
        source_state="failed",
        cloud_gate={
            "protected_root_overlap": True,
            "protected_root_labels": labels,
            "paths_redacted": True,
        },
    )


def _iter_explicit_input_files(
    input_paths: list[str],
    *,
    protected_roots: list[tuple[str, Path]] | None = None,
) -> tuple[list[Path], dict[str, int], list[SourceFileDecision]]:
    roots = protected_roots or protected_input_roots()
    discovered: list[Path] = []
    missing_decisions: list[SourceFileDecision] = []
    counts = {
        "missing_input_count": 0,
        "directory_inputs": 0,
        "file_inputs": 0,
        "unsupported_files": 0,
        "protected_child_count": 0,
    }
    for index, raw in enumerate(input_paths, start=1):
        try:
            path = Path(raw).expanduser().resolve()
        except OSError:
            path = Path(raw).expanduser().absolute()
        if not path.exists():
            counts["missing_input_count"] += 1
            missing_decisions.append(
                SourceFileDecision(
                    safe_label=f"missing_input_{index:03d}",
                    eligible=False,
                    reason="source_missing",
                    source_state="failed",
                )
            )
            continue
        if path.is_dir():
            counts["directory_inputs"] += 1
            try:
                children = sorted(path.iterdir(), key=lambda p: p.name.casefold())
            except OSError:
                counts["missing_input_count"] += 1
                missing_decisions.append(
                    SourceFileDecision(
                        safe_label=f"missing_input_{index:03d}",
                        eligible=False,
                        reason="unreadable_source",
                        source_state="failed",
                    )
                )
                continue
            for item in children:
                try:
                    resolved_item = item.resolve()
                    item_is_file = resolved_item.is_file()
                except OSError:
                    counts["missing_input_count"] += 1
                    missing_decisions.append(
                        SourceFileDecision(
                            safe_label=f"missing_input_{index:03d}",
                            eligible=False,
                            reason="unreadable_source",
                            source_state="failed",
                        )
                    )
                    continue
                if not item_is_file:
                    continue
                labels = protected_root_labels_for_path(resolved_item, roots)
                if labels:
                    counts["protected_child_count"] += 1
                    missing_decisions.append(
                        _protected_input_decision(
                            f"protected_input_{counts['protected_child_count']:03d}",
                            labels,
                        )
                    )
                    continue
                if resolved_item.suffix.casefold() in SUPPORTED_EXTENSIONS:
                    discovered.append(resolved_item)
                else:
                    counts["unsupported_files"] += 1
        elif path.is_file():
            counts["file_inputs"] += 1
            labels = protected_root_labels_for_path(path, roots)
            if labels:
                counts["protected_child_count"] += 1
                missing_decisions.append(
                    _protected_input_decision(
                        f"protected_input_{counts['protected_child_count']:03d}",
                        labels,
                    )
                )
            elif path.suffix.casefold() in SUPPORTED_EXTENSIONS:
                discovered.append(path)
            else:
                counts["unsupported_files"] += 1
        else:
            counts["missing_input_count"] += 1
            missing_decisions.append(
                SourceFileDecision(
                    safe_label=f"missing_input_{index:03d}",
                    eligible=False,
                    reason="source_not_file",
                    source_state="failed",
                )
            )
    return discovered, counts, missing_decisions


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def discover_input_candidates(
    input_paths: list[str],
    *,
    max_items: int,
    min_stable_age_seconds: int = 60,
    stability_wait_seconds: float = 0.25,
) -> PreflightResult:
    allowed_input_paths, protected_input_gate = split_protected_input_paths(input_paths)
    if not protected_input_gate["passed"]:
        allowed_input_paths = []
    discovered, counts, missing_decisions = _iter_explicit_input_files(
        allowed_input_paths,
        protected_roots=protected_input_roots(),
    )
    if counts["protected_child_count"]:
        protected_input_gate = dict(protected_input_gate)
        protected_input_gate["passed"] = False
        protected_input_gate["blocked_count"] = int(protected_input_gate.get("blocked_count") or 0) + counts[
            "protected_child_count"
        ]
        protected_input_gate["child_blocked_count"] = counts["protected_child_count"]
    unique = _dedupe_paths(discovered)
    over_cap_count = max(0, len(unique) - max_items)
    decisions: list[SourceFileDecision] = list(missing_decisions)
    eligible_entries: list[tuple[Path, SourceFileDecision]] = []

    if over_cap_count == 0:
        for index, path in enumerate(unique, start=1):
            decision = evaluate_source_file(
                path,
                safe_label=f"item_{index:03d}",
                min_stable_age_seconds=min_stable_age_seconds,
                stability_wait_seconds=stability_wait_seconds,
            )
            decisions.append(decision)
            if decision.eligible:
                eligible_entries.append((path, decision))

    candidates = [
        pilot.SelectedCandidate(
            label=f"item_{index:03d}",
            path=path,
            suffix=path.suffix.casefold(),
            size_bytes=int(decision.size_bytes or 0),
        )
        for index, (path, decision) in enumerate(eligible_entries, start=1)
    ]
    preflight_counts = public_file_decision_counts(decisions)
    skipped_by_reason = preflight_counts.get("reason_counts", {})
    source_file_preflight = {
        "reported": True,
        "local_files_only": True,
        "evaluated_count": preflight_counts.get("evaluated_count", 0),
        "eligible_count": preflight_counts.get("eligible_count", 0),
        "skipped_count": preflight_counts.get("skipped_count", 0),
        "failed_count": sum(
            count
            for reason, count in dict(skipped_by_reason).items()
            if reason in {
                "unreadable_source",
                "source_missing",
                "source_not_file",
                "protected_app_storage_input",
            }
        ),
        "cloud_placeholder_skipped": sum(
            count
            for reason, count in dict(skipped_by_reason).items()
            if str(reason).startswith("cloud_")
        ),
        "zero_byte_skipped": int(dict(skipped_by_reason).get("zero_byte_file", 0)),
        "unstable_or_recent_skipped": int(dict(skipped_by_reason).get("unstable_size_or_mtime", 0))
        + int(dict(skipped_by_reason).get("recently_modified", 0)),
        "hidden_temp_system_skipped": int(
            dict(skipped_by_reason).get("hidden_temp_system_or_placeholder", 0)
        ),
        "unsupported_extension_skipped": counts["unsupported_files"],
        "protected_input_blocked_count": int(protected_input_gate.get("blocked_count") or 0),
        "protected_input_child_blocked_count": int(protected_input_gate.get("child_blocked_count") or 0),
        "reason_counts": dict(sorted(dict(skipped_by_reason).items())),
        "public_item_results": preflight_counts.get("public_item_results", []),
        "paths_redacted": True,
    }
    scope = {
        "input_mode": "input_path",
        "explicit_input_path_supplied": bool(input_paths),
        "explicit_input_path_redacted": True,
        "input_path_count": len(input_paths),
        "file_inputs": counts["file_inputs"],
        "directory_inputs": counts["directory_inputs"],
        "missing_input_count": counts["missing_input_count"],
        "discovered_files": len(unique),
        "supported_files": len(unique),
        "unsupported_files": counts["unsupported_files"],
        "selected_count": len(candidates),
        "selected_labels": [candidate.label for candidate in candidates],
        "over_cap_count": over_cap_count,
        "over_cap_blocked_before_selection": bool(over_cap_count),
        "default_truncation_disabled": True,
        "max_items": max_items,
        "max_items_cap": MAX_ALLOWED_ITEMS,
        "no_full_library_fallback": True,
        "protected_input_gate": protected_input_gate,
        "private_locator_values_recorded": False,
        "public_path_redaction": "paths_and_filenames_redacted",
        "source_file_preflight_eligible_count": len(candidates),
        "source_file_preflight_skipped_count": source_file_preflight["skipped_count"],
    }
    return PreflightResult(
        candidates=candidates,
        scope=scope,
        source_file_preflight=source_file_preflight,
    )


def provider_availability_summary() -> dict[str, Any]:
    try:
        import onnxruntime as rt

        providers = list(rt.get_available_providers())
        return {
            "reported": True,
            "available_providers": providers,
            "directml_available": "DmlExecutionProvider" in providers,
            "cpu_available": "CPUExecutionProvider" in providers,
            "cpu_fallback_available": "CPUExecutionProvider" in providers,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "reported": True,
            "available_providers": [],
            "directml_available": False,
            "cpu_available": False,
            "cpu_fallback_available": False,
            "error": exc.__class__.__name__,
        }


def job_concurrency_preflight(db: Any) -> dict[str, Any]:
    from backend.app.models import AITagJob, ScanJob
    from backend.app.services.ai_tagging_job_service import is_ai_job_active

    active_statuses = ["pending", "running", "cancelling"]
    scan_jobs = (
        db.query(ScanJob)
        .filter(ScanJob.status.in_(active_statuses))
        .count()
    )
    ai_job_rows = (
        db.query(AITagJob)
        .filter(AITagJob.status.in_(active_statuses))
        .count()
    )
    ai_memory_active = is_ai_job_active()
    return {
        "reported": True,
        "single_active_sync_job_policy": True,
        "active_import_jobs": int(scan_jobs or 0),
        "active_ai_tagging_jobs": int(ai_job_rows or 0),
        "active_ai_tagging_memory_job": bool(ai_memory_active),
        "no_concurrent_import_or_tagging_jobs": (
            int(scan_jobs or 0) == 0 and int(ai_job_rows or 0) == 0 and not ai_memory_active
        ),
        "background_job_started_by_runner": False,
    }


def build_write_preconditions(
    *,
    scope: Mapping[str, Any],
    source_file_preflight: Mapping[str, Any],
    model_cache: Mapping[str, Any],
    provider_availability: Mapping[str, Any],
    provider_preference: str,
    job_concurrency: Mapping[str, Any],
    s3b_scaffold: Mapping[str, Any],
    write_requested: bool,
    write_confirmed: bool,
    local_files_only: bool,
) -> dict[str, Any]:
    blockers: list[str] = []
    requested_providers = provider_list(provider_preference)
    s3b_policy = s3b_scaffold.get("policy", {}) if isinstance(s3b_scaffold.get("policy", {}), Mapping) else {}
    s3b_trigger = s3b_scaffold.get("trigger", {}) if isinstance(s3b_scaffold.get("trigger", {}), Mapping) else {}
    if not write_requested:
        blockers.append("write_not_requested")
    if write_requested and not write_confirmed:
        blockers.append("exact_confirmation_missing")
    if not bool(scope.get("explicit_input_path_supplied")):
        blockers.append("explicit_input_path_missing")
    if int(scope.get("missing_input_count") or 0) > 0:
        blockers.append("explicit_input_missing_or_disappeared")
    if not bool(scope.get("no_full_library_fallback")):
        blockers.append("full_library_fallback_not_allowed")
    if not bool(scope.get("protected_input_gate", {}).get("passed", False)):
        blockers.append("protected_input_root_not_allowed")
    if int(scope.get("selected_count") or 0) <= 0:
        blockers.append("no_eligible_input_files")
    if int(scope.get("over_cap_count") or 0) > 0:
        blockers.append("input_over_cap")
    if int(scope.get("max_items") or 0) > MAX_ALLOWED_ITEMS:
        blockers.append("max_items_exceeds_phase_cap")
    if int(source_file_preflight.get("failed_count") or 0) > 0:
        blockers.append("source_file_preflight_failures")
    if not local_files_only:
        blockers.append("model_download_not_allowed")
    if model_cache.get("status") != "cached":
        blockers.append("model_cache_not_cached")
    if not provider_preference_is_bounded_directml_cpu(provider_preference):
        blockers.append("provider_preference_not_dml_then_cpu")
    if not bool(provider_availability.get("directml_available")):
        blockers.append("directml_provider_unavailable")
    if not bool(provider_availability.get("cpu_fallback_available")):
        blockers.append("cpu_fallback_provider_unavailable")
    if not bool(job_concurrency.get("no_concurrent_import_or_tagging_jobs")):
        blockers.append("concurrent_import_or_tagging_job_active")
    s3b_disabled = (
        s3b_scaffold.get("status") == "disabled_scaffold_ready"
        and not bool(s3b_policy.get("unattended_enabled"))
        and not bool(s3b_policy.get("scheduled_enabled"))
        and bool(s3b_policy.get("dry_run_only"))
        and not bool(s3b_trigger.get("scheduler_started"))
        and not bool(s3b_trigger.get("background_job_started"))
        and not bool(s3b_scaffold.get("automatic_writes_started"))
    )
    if not s3b_disabled:
        blockers.append("s3b_unattended_or_scheduled_state_not_disabled")
    return {
        "reported": True,
        "passed": write_requested and write_confirmed and not blockers,
        "blockers": blockers,
        "write_requested": write_requested,
        "exact_confirmation_present": write_confirmed,
        "input_path_mode": True,
        "local_files_only": local_files_only,
        "model_cache_cached": model_cache.get("status") == "cached",
        "model_download_not_allowed": local_files_only,
        "scope_valid": (
            bool(scope.get("explicit_input_path_supplied"))
            and int(scope.get("missing_input_count") or 0) == 0
            and bool(scope.get("no_full_library_fallback"))
        ),
        "no_over_cap_input": int(scope.get("over_cap_count") or 0) == 0,
        "no_full_library_fallback": bool(scope.get("no_full_library_fallback")),
        "protected_input_gate_passed": bool(scope.get("protected_input_gate", {}).get("passed", False)),
        "protected_input_blocked_count": int(scope.get("protected_input_gate", {}).get("blocked_count") or 0),
        "requested_provider_preference": requested_providers,
        "provider_preference_dml_then_cpu": provider_preference_is_bounded_directml_cpu(provider_preference),
        "directml_provider_available": bool(provider_availability.get("directml_available")),
        "cpu_fallback_available": bool(provider_availability.get("cpu_fallback_available")),
        "no_concurrent_import_or_tagging_jobs": bool(
            job_concurrency.get("no_concurrent_import_or_tagging_jobs")
        ),
        "s3b_disabled_state_passed": s3b_disabled,
        "s3b_unattended_enabled": bool(s3b_policy.get("unattended_enabled")),
        "s3b_scheduled_enabled": bool(s3b_policy.get("scheduled_enabled")),
        "s3b_scheduler_started": bool(s3b_trigger.get("scheduler_started")),
        "s3b_background_job_started": bool(s3b_trigger.get("background_job_started")),
        "s3b_automatic_writes_started": bool(s3b_scaffold.get("automatic_writes_started")),
    }


def _default_stage(name: str) -> dict[str, Any]:
    return {"reported": False, "status": "not_run", "stage": name}


def _not_run_ai_result(
    status: str,
    *,
    label: str = "directml_primary",
    provider_preference: str = DEFAULT_PROVIDER_PREFERENCE,
    selected_media_count: int = 0,
) -> dict[str, Any]:
    return {
        "reported": True,
        "executed": False,
        "label": label,
        "status": status,
        "dry_run": True,
        "local_files_only": True,
        "provider_preference_requested": provider_list(provider_preference),
        "selected_media_count": selected_media_count,
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
        "first_time_media_tag_insertion_count": 0,
        "no_media_tags_writes": True,
        "provider": {},
        "load_control": {},
    }


def build_provider_write_gate(
    *,
    provider_availability: Mapping[str, Any],
    provider_preference: str,
    directml_probe: Mapping[str, Any],
    write_requested: bool,
    write_confirmed: bool,
    write_preconditions_passed: bool,
) -> dict[str, Any]:
    requested = provider_list(provider_preference)
    preference_dml_then_cpu = requested == ["DmlExecutionProvider", "CPUExecutionProvider"]
    directml_available = bool(provider_availability.get("directml_available"))
    cpu_available = bool(provider_availability.get("cpu_fallback_available"))
    probe_executed = bool(directml_probe.get("executed"))
    probe_status = directml_probe.get("status")
    probe_failed = int(directml_probe.get("failed", 0) or 0)
    probe_rollback_error = bool(directml_probe.get("rollback_error"))
    probe_error_state = bool(directml_probe.get("error_state"))
    probe_actual = directml_probe.get("provider", {}).get("actual_provider")
    blockers: list[str] = []

    if not write_requested:
        blockers.append("write_not_requested")
    if write_requested and not write_confirmed:
        blockers.append("exact_confirmation_missing")
    if write_requested and write_confirmed and not write_preconditions_passed:
        blockers.append("write_preconditions_not_passed")
    if write_requested and write_confirmed and not preference_dml_then_cpu:
        blockers.append("provider_preference_not_dml_then_cpu")
    if write_requested and write_confirmed and not directml_available:
        blockers.append("directml_provider_unavailable")
    if write_requested and write_confirmed and not cpu_available:
        blockers.append("cpu_fallback_provider_unavailable")
    if write_requested and write_confirmed and write_preconditions_passed and preference_dml_then_cpu and directml_available:
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

    passed = bool(write_requested and write_confirmed and write_preconditions_passed and not blockers)
    return {
        "reported": True,
        "write_requested": write_requested,
        "exact_confirmation_present": write_confirmed,
        "write_preconditions_passed": write_preconditions_passed,
        "requested_provider_preference": requested,
        "actual_write_provider_preference": provider_list(DIRECTML_WRITE_PROVIDER_PREFERENCE),
        "actual_write_requires_directml_only": True,
        "cpu_fallback_write_allowed": False,
        "cpu_fallback_validation_dry_run_only": True,
        "requires_directml_provider": True,
        "provider_preference_includes_directml": "DmlExecutionProvider" in requested,
        "provider_preference_includes_cpu_fallback": "CPUExecutionProvider" in requested,
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
        "blockers": blockers,
    }


def build_write_provider_policy(provider_preference: str) -> dict[str, Any]:
    return {
        "reported": True,
        "prewrite_probe_provider_preference": provider_list(provider_preference),
        "actual_write_provider_preference": provider_list(DIRECTML_WRITE_PROVIDER_PREFERENCE),
        "actual_write_requires_directml_only": True,
        "provider_fallback_disabled_for_actual_write": True,
        "cpu_fallback_write_allowed": False,
        "cpu_fallback_validation_dry_run_only": True,
        "no_cpu_fallback_write_path": True,
    }


def _job_recheck_not_run(status: str, stage: str) -> dict[str, Any]:
    return {
        "reported": True,
        "stage": stage,
        "executed": False,
        "status": status,
        "passed": False,
        "no_concurrent_import_or_tagging_jobs": False,
        "active_import_jobs": None,
        "active_ai_tagging_jobs": None,
        "active_ai_tagging_memory_job": None,
    }


def job_concurrency_recheck(db: Any, *, stage: str) -> dict[str, Any]:
    result = dict(job_concurrency_preflight(db))
    passed = bool(result.get("no_concurrent_import_or_tagging_jobs"))
    result.update(
        {
            "stage": stage,
            "executed": True,
            "status": "passed" if passed else "blocked_concurrent_job_active",
            "passed": passed,
        }
    )
    return result


def build_write_window_protection(
    *,
    write_requested: bool,
    write_confirmed: bool,
    write_preconditions_passed: bool,
    import_recheck: Mapping[str, Any] | None = None,
    ai_write_recheck: Mapping[str, Any] | None = None,
    operator_lock: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    import_check = dict(import_recheck or _job_recheck_not_run("not_run", "before_import_write"))
    ai_check = dict(ai_write_recheck or _job_recheck_not_run("not_run", "before_ai_write"))
    lock_state = dict(operator_lock or OperatorSyncLock().public_state())
    write_path_active = bool(write_requested and write_confirmed and write_preconditions_passed)
    blockers: list[str] = []
    if write_path_active:
        if not bool(lock_state.get("acquired")):
            blockers.append("operator_sync_lock_not_held")
        if not bool(import_check.get("passed")):
            blockers.append("import_write_concurrency_recheck_not_passed")
        if not bool(ai_check.get("passed")):
            blockers.append("ai_write_concurrency_recheck_not_passed")
    rechecked = bool(import_check.get("executed")) and bool(ai_check.get("executed"))
    no_concurrent = (
        bool(lock_state.get("acquired"))
        and bool(import_check.get("passed"))
        and bool(ai_check.get("passed"))
    )
    mode = (
        "lock_file_atomic_create_plus_immediate_recheck"
        if bool(lock_state.get("acquired"))
        else "immediate_recheck_no_durable_lock"
    )
    return {
        "reported": True,
        "mode": mode,
        "durable_lock_held": bool(lock_state.get("acquired")),
        "durable_lock_deferred": False,
        "lock_scope": lock_state.get("lock_scope"),
        "operator_sync_lock": lock_state,
        "write_requested": write_requested,
        "exact_confirmation_present": write_confirmed,
        "write_preconditions_passed": write_preconditions_passed,
        "write_window_rechecked": rechecked,
        "no_concurrent_import_or_tagging_jobs": no_concurrent,
        "import_recheck": import_check,
        "ai_write_recheck": ai_check,
        "blockers": blockers,
    }


def add_write_precondition_blocker(preconditions: dict[str, Any], blocker: str) -> dict[str, Any]:
    blockers = list(preconditions.get("blockers") or [])
    if blocker not in blockers:
        blockers.append(blocker)
    updated = dict(preconditions)
    updated["passed"] = False
    updated["blockers"] = blockers
    if blocker.startswith("concurrent_"):
        updated["no_concurrent_import_or_tagging_jobs"] = False
    return updated


def _augment_ai_result(result: dict[str, Any]) -> dict[str, Any]:
    delta = int(result.get("media_with_ai_tags_delta") or 0)
    result["first_time_media_tag_insertion_count"] = max(0, delta)
    if result.get("first_time_media_tag_insertion_proven") is None:
        result["first_time_media_tag_insertion_proven"] = delta > 0
    return result


def validate_localization(db: Any, touched_tags: list[str]) -> dict[str, Any]:
    result = pilot.validate_localization_reuse(db, touched_tags)
    if result.get("deferred_reason") == "external_llm_provider_not_approved_for_s3a_pilot1":
        result["deferred_reason"] = "external_llm_provider_not_approved_for_s3a_prod2_s3b_d1"
    result["deferred_reason"] = (
        result.get("deferred_reason")
        or "external_llm_provider_not_approved_for_s3a_prod2_s3b_d1"
    )
    result["external_provider_used"] = bool(result.get("llm_external_provider_used", False))
    return result


def build_s3a_boundary(import_executed: bool, ai_write_executed: bool) -> dict[str, Any]:
    return {
        "operator_triggered_only": True,
        "production_execution_enabled": False,
        "unattended_enabled": False,
        "scheduled_automation_enabled": False,
        "broad_production_sync_enabled": False,
        "no_full_library_fallback": True,
        "stages": [
            {"name": "preflight", "operator_write_executed": False},
            {"name": "import_reuse", "operator_write_executed": import_executed},
            {"name": "classification", "operator_write_executed": False},
            {"name": "directml_ai_tagging", "operator_write_executed": ai_write_executed},
            {"name": "cpu_fallback", "operator_write_executed": False},
            {"name": "localization", "operator_write_executed": False},
            {"name": "s3b_disabled_scaffold", "operator_write_executed": False},
        ],
    }


def derive_status(summary: Mapping[str, Any]) -> str:
    config = summary.get("run_configuration", {})
    scope = summary.get("scope", {})
    model_cache = summary.get("model_cache", {})
    preconditions = summary.get("write_preconditions", {})
    import_reuse = summary.get("import_reuse", {})
    classification = summary.get("classification", {})
    ai = summary.get("directml_ai_tagging", {})
    probe = summary.get("directml_provider_probe", {})
    gate = summary.get("provider_write_gate", {})
    cpu = summary.get("cpu_fallback_validation", {})
    s3b = summary.get("s3b_disabled_scaffold", {})
    window = summary.get("write_window_protection", {})

    if not bool(summary.get("public_redaction", {}).get("passed", True)):
        return "blocked_public_redaction_failed"
    if int(config.get("max_items") or 0) > MAX_ALLOWED_ITEMS:
        return "blocked_max_items_over_phase_cap"
    if int(scope.get("over_cap_count") or 0) > 0:
        return "blocked_input_over_cap"
    if not bool(scope.get("explicit_input_path_supplied")):
        return "blocked_scope_invalid"
    if int(scope.get("protected_input_gate", {}).get("blocked_count") or 0) > 0:
        return "blocked_protected_input_root"
    if not bool(scope.get("no_full_library_fallback")):
        return "blocked_full_library_fallback"
    if not bool(config.get("local_files_only")) or bool(config.get("model_download_allowed")):
        return "blocked_model_download_allowed"
    if model_cache.get("status") != "cached":
        return "blocked_model_cache_missing"
    if not bool(preconditions.get("provider_preference_dml_then_cpu", True)):
        return "blocked_provider_preference_invalid"
    if not bool(summary.get("provider_availability", {}).get("directml_available")):
        return "blocked_directml_provider_not_available"
    if not bool(summary.get("provider_availability", {}).get("cpu_fallback_available", True)):
        return "blocked_cpu_fallback_provider_not_available"
    job_concurrency = summary.get("job_concurrency", {}) if isinstance(summary.get("job_concurrency", {}), Mapping) else {}
    if bool(job_concurrency.get("operator_sync_lock_acquisition_attempted")) and not bool(
        job_concurrency.get("operator_sync_lock_acquired")
    ):
        return "blocked_concurrent_operator_sync"
    if not bool(job_concurrency.get("no_concurrent_import_or_tagging_jobs")):
        return "blocked_concurrent_job_active"
    if int(summary.get("source_file_preflight", {}).get("failed_count") or 0) > 0:
        return "blocked_source_file_preflight_failures"
    if bool(config.get("write_requested")) and not bool(config.get("operator_confirmation_exact")):
        return "blocked_write_requested_without_exact_confirmation"
    if int(scope.get("selected_count") or 0) <= 0:
        return "blocked_no_media"
    if bool(config.get("write_requested")) and not bool(preconditions.get("s3b_disabled_state_passed", True)):
        return "blocked_s3b_scaffold_not_disabled"
    if bool(config.get("write_requested")) and bool(config.get("operator_confirmation_exact")) and not bool(
        preconditions.get("passed")
    ):
        return "blocked_write_preconditions"
    if bool(config.get("write_requested")) and bool(config.get("operator_confirmation_exact")):
        if not bool(window.get("durable_lock_held")):
            return "blocked_concurrent_operator_sync"
        import_recheck = window.get("import_recheck", {}) if isinstance(window.get("import_recheck", {}), Mapping) else {}
        ai_recheck = window.get("ai_write_recheck", {}) if isinstance(window.get("ai_write_recheck", {}), Mapping) else {}
        if bool(import_recheck.get("executed")) and not bool(import_recheck.get("passed")):
            return "blocked_write_window_concurrency"
        if bool(ai_recheck.get("executed")) and not bool(ai_recheck.get("passed")):
            return "blocked_write_window_concurrency"
    if int(import_reuse.get("failed_count") or 0) > 0:
        return "blocked_import_item_failures"
    if int(classification.get("failed_count") or 0) > 0:
        return "blocked_classification_failures"
    if int(ai.get("failed") or 0) > 0:
        return "blocked_ai_tagging_item_failures"
    if int(cpu.get("failed") or 0) > 0 or cpu.get("status") != "completed":
        return "blocked_cpu_fallback_not_validated"
    if s3b.get("status") != "disabled_scaffold_ready":
        return "blocked_s3b_scaffold_not_disabled"

    if bool(config.get("write_requested")) and bool(config.get("operator_confirmation_exact")):
        if not bool(import_reuse.get("executed")) or not bool(ai.get("executed")) or bool(ai.get("dry_run")):
            return "blocked_write_not_executed"
        if not bool(summary.get("provider_write_gate", {}).get("passed")):
            return "blocked_directml_provider_not_validated"
        if ai.get("provider", {}).get("actual_provider") != "DmlExecutionProvider":
            return "blocked_directml_provider_not_validated"
        if int(scope.get("selected_count") or 0) < 2:
            return "write_executed_but_batch_scale_unproven"
        if int(ai.get("first_time_media_tag_insertion_count") or 0) <= 0 and int(
            ai.get("media_tags_count_delta") or 0
        ) <= 0:
            return "write_executed_but_first_time_insertion_unproven"
        return "target_met_with_bounded_write"

    if ai.get("provider", {}).get("actual_provider") != "DmlExecutionProvider":
        return "blocked_directml_provider_not_validated"
    return "target_met_dry_run_only"


def apply_pipeline_status(summary: dict[str, Any], status: str) -> None:
    claims = {
        "target_met": status in TARGET_STATUSES,
        "safe_to_merge": status in TARGET_STATUSES,
        "full_chain_complete": status in TARGET_STATUSES,
    }
    summary["pipeline_contract"] = {
        "contract_id": CONTRACT_ID,
        "status": status,
        "claims": claims,
    }


def build_failure_budget(summary: Mapping[str, Any]) -> dict[str, Any]:
    public_redaction_count = int(summary.get("public_redaction", {}).get("finding_count") or 0)
    checks = {
        "import_failed_count": int(summary.get("import_reuse", {}).get("failed_count") or 0),
        "classification_failed_count": int(summary.get("classification", {}).get("failed_count") or 0),
        "ai_tagging_failed": int(summary.get("directml_ai_tagging", {}).get("failed") or 0),
        "cpu_fallback_failed": int(summary.get("cpu_fallback_validation", {}).get("failed") or 0),
        "public_redaction_finding_count": public_redaction_count,
    }
    return {
        **checks,
        "passed": all(value == 0 for value in checks.values()),
        "target_requires_all_zero": True,
    }


def build_safety(summary: Mapping[str, Any]) -> dict[str, Any]:
    config = summary.get("run_configuration", {})
    scope = summary.get("scope", {})
    s3b = summary.get("s3b_disabled_scaffold", {})
    return {
        "max_items_lte_20": int(config.get("max_items") or 0) <= MAX_ALLOWED_ITEMS,
        "selected_input_explicit_bounded": bool(scope.get("explicit_input_path_supplied"))
        and bool(scope.get("no_full_library_fallback"))
        and int(scope.get("selected_count") or 0) <= int(config.get("max_items") or 0),
        "no_full_library_run": True,
        "no_full_library_fallback": True,
        "write_without_confirmation": bool(config.get("write_requested"))
        and not bool(config.get("operator_confirmation_exact")),
        "source_icloud_mutation": False,
        "source_mutation": False,
        "app_managed_storage_writes_only_after_gates": True,
        "production_s3a_automation_enabled": False,
        "unattended_s3b_enabled": bool(
            s3b.get("policy", {}).get("unattended_enabled", False)
        ),
        "scheduled_s3b_enabled": bool(s3b.get("policy", {}).get("scheduled_enabled", False)),
        "scheduler_started": bool(s3b.get("scheduler_started", False)),
        "background_job_started": bool(s3b.get("background_job_started", False)),
        "automatic_writes_started": bool(s3b.get("automatic_writes_started", False)),
        "provider_pixiv_gallery_dl_saucenao_google_calls": False,
        "sourceconcept_r1_r2_r1r": False,
        "entity_bridge": False,
        "confirmed_entity_assignments": False,
        "desired_media_backfill": False,
        "cleanup_delete_reset_drop_truncate": False,
        "drop_truncate_reset": False,
        "model_download": bool(config.get("model_download_allowed", False)),
        "local_files_only": bool(config.get("local_files_only", False)),
        "public_redaction_passed": bool(summary.get("public_redaction", {}).get("passed", False)),
        "private_locator_values_recorded": False,
        "external_llm_provider_used": bool(summary.get("localization", {}).get("llm_external_provider_used", False)),
        "no_db_import": not bool(summary.get("import_reuse", {}).get("executed", False)),
        "no_classification": False,
        "no_ai_tagging": not (
            bool(summary.get("directml_ai_tagging", {}).get("executed", False))
            and not bool(summary.get("directml_ai_tagging", {}).get("dry_run", True))
        ),
        "no_entity_resolver_similarity": True,
    }


def render_markdown(summary: Mapping[str, Any]) -> str:
    status = summary.get("pipeline_contract", {}).get("status")
    scope = summary.get("scope", {})
    preflight = summary.get("source_file_preflight", {})
    import_reuse = summary.get("import_reuse", {})
    classification = summary.get("classification", {})
    ai = summary.get("directml_ai_tagging", {})
    probe = summary.get("directml_provider_probe", {})
    gate = summary.get("provider_write_gate", {})
    cpu = summary.get("cpu_fallback_validation", {})
    loc = summary.get("localization", {})
    failure_budget = summary.get("failure_budget", {})
    load = summary.get("load_control_observations", {})
    s3b = summary.get("s3b_disabled_scaffold", {})
    protected_gate = scope.get("protected_input_gate", {}) if isinstance(scope.get("protected_input_gate", {}), Mapping) else {}
    write_provider_policy = summary.get("write_provider_policy", {})
    write_window = summary.get("write_window_protection", {})
    provider = ai.get("provider", {}) if isinstance(ai.get("provider", {}), Mapping) else {}
    return "\n".join(
        [
            "# S3A-PROD2/S3B-D1: Bounded Operator Sync Scale-Up and Disabled Unattended Sync Design",
            "",
            f"Status: `{status}`.",
            "",
            f"Contract: `{CONTRACT_ID}`.",
            "",
            f"Public summary: `{repo_relative(SUMMARY_PATH)}`.",
            "",
            "## Operator Scope",
            "",
            f"- Input mode: `{scope.get('input_mode')}`.",
            f"- Selected input count: `{scope.get('selected_count')}`.",
            f"- Discovered supported files: `{scope.get('supported_files')}`.",
            f"- Over-cap count: `{scope.get('over_cap_count')}`.",
            f"- Max items: `{summary.get('run_configuration', {}).get('max_items')}`.",
            f"- Full-library fallback: `{not bool(scope.get('no_full_library_fallback'))}`.",
            f"- Public path redaction: `{scope.get('public_path_redaction')}`.",
            f"- Protected input gate passed: `{protected_gate.get('passed')}`.",
            f"- Protected input blocked count: `{protected_gate.get('blocked_count')}`.",
            "",
            "## Source File Preflight",
            "",
            f"- Evaluated: `{preflight.get('evaluated_count')}`.",
            f"- Eligible: `{preflight.get('eligible_count')}`.",
            f"- Skipped: `{preflight.get('skipped_count')}`.",
            f"- Failed: `{preflight.get('failed_count')}`.",
            f"- Cloud placeholder skipped: `{preflight.get('cloud_placeholder_skipped')}`.",
            f"- Zero-byte skipped: `{preflight.get('zero_byte_skipped')}`.",
            f"- Unstable/recent skipped: `{preflight.get('unstable_or_recent_skipped')}`.",
            f"- Hidden/temp/system skipped: `{preflight.get('hidden_temp_system_skipped')}`.",
            "",
            "## Import / Reuse",
            "",
            f"- Executed write: `{import_reuse.get('executed')}`.",
            f"- Exact confirmation present: `{summary.get('run_configuration', {}).get('operator_confirmation_exact')}`.",
            f"- Imported: `{import_reuse.get('imported_count')}`.",
            f"- Reused: `{import_reuse.get('reused_count')}`.",
            f"- Skipped: `{import_reuse.get('skipped_count')}`.",
            f"- Failed: `{import_reuse.get('failed_count')}`.",
            f"- App-managed storage writes: `{import_reuse.get('app_managed_storage_writes')}`.",
            f"- Source/iCloud mutation: `{import_reuse.get('source_icloud_mutation')}`.",
            "",
            "## Classification",
            "",
            f"- Executed: `{classification.get('executed')}`.",
            f"- Classified: `{classification.get('classified_count')}`.",
            f"- Reused classification: `{classification.get('reused_classification_count')}`.",
            f"- Failed: `{classification.get('failed_count')}`.",
            f"- Distribution: `{classification.get('content_class_distribution')}`.",
            "",
            "## DirectML AI Tagging",
            "",
            f"- Executed: `{ai.get('executed')}`.",
            f"- Dry run: `{ai.get('dry_run')}`.",
            f"- Provider preference: `{ai.get('provider_preference_requested')}`.",
            f"- Actual write provider preference: `{write_provider_policy.get('actual_write_provider_preference')}`.",
            f"- CPU fallback write allowed: `{write_provider_policy.get('cpu_fallback_write_allowed')}`.",
            f"- Actual provider: `{provider.get('actual_provider')}`.",
            f"- Processed: `{ai.get('processed')}`.",
            f"- Failed: `{ai.get('failed')}`.",
            f"- Tags added: `{ai.get('tags_added')}`.",
            f"- Suggestions added: `{ai.get('suggestions_added')}`.",
            f"- Skipped locked: `{ai.get('skipped_locked')}`.",
            f"- Ignored low confidence: `{ai.get('ignored_low_confidence')}`.",
            f"- Media tags delta: `{ai.get('media_tags_count_delta')}`.",
            f"- Media with AI tags delta: `{ai.get('media_with_ai_tags_delta')}`.",
            f"- First-time insertion count: `{ai.get('first_time_media_tag_insertion_count')}`.",
            f"- Prewrite DirectML probe status: `{probe.get('status')}`.",
            f"- Prewrite DirectML probe provider: `{probe.get('provider', {}).get('actual_provider')}`.",
            f"- Provider write gate passed: `{gate.get('passed')}`.",
            f"- Provider write gate blockers: `{gate.get('blockers')}`.",
            f"- Write window protection mode: `{write_window.get('mode')}`.",
            f"- Durable lock held: `{write_window.get('durable_lock_held')}`.",
            f"- Write window rechecked: `{write_window.get('write_window_rechecked')}`.",
            f"- Write window blockers: `{write_window.get('blockers')}`.",
            "",
            "## CPU Fallback",
            "",
            f"- Executed: `{cpu.get('executed')}`.",
            f"- Status: `{cpu.get('status')}`.",
            f"- Actual provider: `{cpu.get('provider', {}).get('actual_provider')}`.",
            f"- Failed: `{cpu.get('failed')}`.",
            f"- Media tags delta: `{cpu.get('media_tags_count_delta')}`.",
            "",
            "## Localization",
            "",
            f"- Reused translations: `{loc.get('reused_translations')}`.",
            f"- New local/static translations: `{loc.get('new_translations')}`.",
            f"- Missing/deferred: `{loc.get('missing_or_deferred')}`.",
            f"- Failed: `{loc.get('failed')}`.",
            f"- External/LLM provider used: `{loc.get('llm_external_provider_used')}`.",
            "",
            "## Failure Budget",
            "",
            f"- Passed: `{failure_budget.get('passed')}`.",
            f"- Import failures: `{failure_budget.get('import_failed_count')}`.",
            f"- Classification failures: `{failure_budget.get('classification_failed_count')}`.",
            f"- AI tagging failures: `{failure_budget.get('ai_tagging_failed')}`.",
            f"- CPU fallback failures: `{failure_budget.get('cpu_fallback_failed')}`.",
            f"- Public redaction findings: `{failure_budget.get('public_redaction_finding_count')}`.",
            "",
            "## Load Control",
            "",
            f"- Effective batch size: `{load.get('effective_batch_size')}`.",
            f"- CPU intra/inter threads: `{load.get('cpu_intra_op_threads')}` / `{load.get('cpu_inter_op_threads')}`.",
            f"- Preprocess workers: `{load.get('preprocess_workers')}`.",
            f"- Max concurrent AI jobs: `{load.get('max_concurrent_ai_jobs')}`.",
            "",
            "## S3B Disabled Scaffold",
            "",
            f"- Scaffold status: `{s3b.get('status')}`.",
            f"- Unattended enabled: `{s3b.get('policy', {}).get('unattended_enabled')}`.",
            f"- Scheduled enabled: `{s3b.get('policy', {}).get('scheduled_enabled')}`.",
            f"- Scheduler started: `{s3b.get('scheduler_started')}`.",
            f"- Background job started: `{s3b.get('background_job_started')}`.",
            f"- Automatic writes started: `{s3b.get('automatic_writes_started')}`.",
            f"- Root count: `{s3b.get('policy', {}).get('root_count')}`.",
            f"- Roots redacted: `{s3b.get('roots_redacted')}`.",
            "",
            "## Cloud / iCloud Policy",
            "",
            f"- Conservative fallback: `{s3b.get('cloud_file_policy', {}).get('conservative_fallback')}`.",
            f"- Paths redacted: `{s3b.get('cloud_file_policy', {}).get('paths_redacted')}`.",
            f"- Source mutation: `{summary.get('safety', {}).get('source_icloud_mutation')}`.",
            "",
            "## Public Redaction",
            "",
            f"- Passed: `{summary.get('public_redaction', {}).get('passed')}`.",
            f"- Finding count: `{summary.get('public_redaction', {}).get('finding_count')}`.",
            "",
            "## Next Recommended Phase",
            "",
            "- Move to a dedicated S3A ledger/watermark production design or a bounded follow-up scale run only after this PR is reviewed and merged.",
            "",
        ]
    )


def write_reports(summary: dict[str, Any]) -> bool:
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    summary["public_redaction"] = {"passed": False, "finding_count": None}
    summary["failure_budget"] = build_failure_budget(summary)
    summary["safety"] = build_safety(summary)
    markdown = render_markdown(summary)

    from scripts.phase_contracts.contract_checks import scan_public_payload

    findings = scan_public_payload(
        {"public_json_payload": summary, "public_markdown_text": markdown}
    )
    summary["public_redaction"] = {
        "passed": not findings,
        "finding_count": len(findings),
        "findings_redacted": True,
        "scan_scope": "public_json_and_markdown",
        "clean_before_public_write": not findings,
        "unsafe_public_report_written": False,
    }
    summary["failure_budget"] = build_failure_budget(summary)
    status = derive_status(summary)
    apply_pipeline_status(summary, status)
    summary["safety"] = build_safety(summary)
    markdown = render_markdown(summary)
    findings = scan_public_payload(
        {"public_json_payload": summary, "public_markdown_text": markdown}
    )
    summary["public_redaction"] = {
        "passed": not findings,
        "finding_count": len(findings),
        "findings_redacted": True,
        "scan_scope": "public_json_and_markdown",
        "clean_before_public_write": not findings,
        "unsafe_public_report_written": False,
    }
    summary["failure_budget"] = build_failure_budget(summary)
    status = derive_status(summary)
    apply_pipeline_status(summary, status)
    summary["safety"] = build_safety(summary)
    if findings:
        apply_pipeline_status(summary, "blocked_public_redaction_failed")
        summary["safety"] = build_safety(summary)
        summary["failure_budget"] = build_failure_budget(summary)
        return False
    SUMMARY_PATH.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    MARKDOWN_PATH.write_text(markdown, encoding="utf-8")
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run S3A-PROD2 bounded operator sync scale-up.")
    parser.add_argument("--input-path", action="append", default=[], help="Explicit local input file or directory. May be repeated.")
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    parser.add_argument("--provider-preference", default=DEFAULT_PROVIDER_PREFERENCE)
    parser.add_argument("--execute-write", action="store_true")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--allow-model-download", action="store_true")
    parser.add_argument("--min-stable-age-seconds", type=int, default=None)
    parser.add_argument("--stability-wait-seconds", type=float, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    max_items = int(args.max_items)
    if not (1 <= max_items <= MAX_ALLOWED_ITEMS):
        raise SystemExit(f"--max-items must be between 1 and {MAX_ALLOWED_ITEMS}.")
    if not args.input_path:
        raise SystemExit("S3A-PROD2 requires --input-path; no full-library fallback is allowed.")

    started_at = utc_now_iso()
    write_requested = bool(args.execute_write)
    write_confirmed = args.confirmation == WRITE_CONFIRMATION
    local_files_only = not args.allow_model_download
    s3b_policy_defaults = SyncPolicy.from_settings()
    min_stable_age_seconds = (
        s3b_policy_defaults.min_stable_age_seconds
        if args.min_stable_age_seconds is None
        else max(0, int(args.min_stable_age_seconds))
    )
    stability_wait_seconds = (
        s3b_policy_defaults.stability_wait_seconds
        if args.stability_wait_seconds is None
        else max(0.0, float(args.stability_wait_seconds))
    )
    s3b_scaffold = build_disabled_scaffold()

    pilot.SOURCE_LABEL = SOURCE_LABEL
    preflight = discover_input_candidates(
        args.input_path,
        max_items=max_items,
        min_stable_age_seconds=min_stable_age_seconds,
        stability_wait_seconds=stability_wait_seconds,
    )

    model_cache: dict[str, Any] = {}
    provider_availability = provider_availability_summary()
    job_concurrency: dict[str, Any] = {
        "reported": True,
        "no_concurrent_import_or_tagging_jobs": False,
        "background_job_started_by_runner": False,
        "error": "not_checked",
    }
    write_preconditions: dict[str, Any] = {"reported": False, "passed": False, "blockers": []}
    import_reuse: dict[str, Any] = _default_stage("import_reuse")
    classification: dict[str, Any] = _default_stage("classification")
    directml_ai_tagging: dict[str, Any] = _not_run_ai_result("not_run")
    directml_provider_probe: dict[str, Any] = _not_run_ai_result(
        "not_required_preflight_only",
        label="directml_prewrite_probe",
        provider_preference=args.provider_preference,
    )
    provider_write_gate: dict[str, Any] = {}
    write_provider_policy = build_write_provider_policy(args.provider_preference)
    import_concurrency_recheck = _job_recheck_not_run("not_run", "before_import_write")
    ai_write_concurrency_recheck = _job_recheck_not_run("not_run", "before_ai_write")
    operator_lock = OperatorSyncLock()
    operator_lock_state = operator_lock.public_state()
    write_window_protection = build_write_window_protection(
        write_requested=write_requested,
        write_confirmed=write_confirmed,
        write_preconditions_passed=False,
        import_recheck=import_concurrency_recheck,
        ai_write_recheck=ai_write_concurrency_recheck,
        operator_lock=operator_lock_state,
    )
    cpu_fallback: dict[str, Any] = _not_run_ai_result("not_run")
    localization: dict[str, Any] = {"reported": True, "status": "not_run", "failed": 0, "llm_external_provider_used": False}
    downstream_media_ids: list[int] = []
    touched_tags: list[str] = []

    with prod2_runtime_env(max_items, args.provider_preference):
        model_cache = pilot.check_model_cache(local_files_only)
        db = pilot.get_db_session()
        try:
            job_concurrency = job_concurrency_preflight(db)
            write_preconditions = build_write_preconditions(
                scope=preflight.scope,
                source_file_preflight=preflight.source_file_preflight,
                model_cache=model_cache,
                provider_availability=provider_availability,
                provider_preference=args.provider_preference,
                job_concurrency=job_concurrency,
                s3b_scaffold=s3b_scaffold,
                write_requested=write_requested,
                write_confirmed=write_confirmed,
                local_files_only=local_files_only,
            )
            if bool(write_preconditions.get("passed")):
                operator_lock_state = operator_lock.acquire()
                job_concurrency = dict(job_concurrency)
                job_concurrency.update(
                    {
                        "operator_sync_lock_acquisition_attempted": True,
                        "operator_sync_lock_acquired": bool(operator_lock_state.get("acquired")),
                        "durable_lock_held": bool(operator_lock_state.get("acquired")),
                        "lock_scope": operator_lock_state.get("lock_scope"),
                        "operator_sync_lock": operator_lock_state,
                        "no_concurrent_import_or_tagging_jobs": bool(
                            job_concurrency.get("no_concurrent_import_or_tagging_jobs")
                        )
                        and bool(operator_lock_state.get("acquired")),
                    }
                )
                if not bool(operator_lock_state.get("acquired")):
                    write_preconditions = add_write_precondition_blocker(
                        write_preconditions,
                        "concurrent_operator_sync_active",
                    )
                    import_concurrency_recheck = _job_recheck_not_run(
                        "not_run_operator_sync_lock_blocked",
                        "before_import_write",
                    )
                else:
                    import_concurrency_recheck = job_concurrency_recheck(db, stage="before_import_write")
                if bool(operator_lock_state.get("acquired")) and not bool(import_concurrency_recheck.get("passed")):
                    write_preconditions = add_write_precondition_blocker(
                        write_preconditions,
                        "concurrent_job_active_before_import_write",
                    )
            else:
                import_concurrency_recheck = _job_recheck_not_run(
                    "not_run_write_preconditions_blocked",
                    "before_import_write",
                )
            write_window_protection = build_write_window_protection(
                write_requested=write_requested,
                write_confirmed=write_confirmed,
                write_preconditions_passed=bool(write_preconditions.get("passed")),
                import_recheck=import_concurrency_recheck,
                ai_write_recheck=ai_write_concurrency_recheck,
                operator_lock=operator_lock_state,
            )
            import_reuse, downstream_media_ids = pilot.import_or_reuse_from_input(
                db,
                preflight.candidates,
                write_requested=write_requested,
                execute_import=bool(write_preconditions.get("passed")),
                import_confirmed=write_confirmed,
                write_preconditions_passed=bool(write_preconditions.get("passed")),
                write_blockers=list(write_preconditions.get("blockers") or []),
            )
            downstream_allowed = bool(downstream_media_ids) and (
                not write_requested or bool(write_preconditions.get("passed"))
            )
            if downstream_allowed:
                classification = pilot.classify_media_scope(db, downstream_media_ids)
            else:
                classification = {
                    "reported": True,
                    "executed": False,
                    "status": "not_run_write_preconditions_blocked"
                    if write_requested and not write_preconditions.get("passed")
                    else "not_run_no_media",
                    "classified_count": 0,
                    "reused_classification_count": 0,
                    "failed_count": 0,
                    "content_class_distribution": {},
                }

            if model_cache.get("status") == "cached" and local_files_only and downstream_allowed:
                if write_requested and write_confirmed:
                    if bool(write_preconditions.get("passed")) and provider_preference_is_bounded_directml_cpu(
                        args.provider_preference
                    ) and bool(provider_availability.get("directml_available")):
                        directml_provider_probe, _probe_tags = pilot.run_ai_tagging_pass(
                            db,
                            label="directml_prewrite_probe",
                            media_ids=downstream_media_ids[:1],
                            dry_run=True,
                            provider_preference=args.provider_preference,
                            max_items=1,
                            local_files_only=local_files_only,
                        )
                        directml_provider_probe = _augment_ai_result(directml_provider_probe)
                    else:
                        directml_provider_probe = _not_run_ai_result(
                            "not_run_write_preconditions_blocked",
                            label="directml_prewrite_probe",
                            provider_preference=args.provider_preference,
                            selected_media_count=min(1, len(downstream_media_ids)),
                        )
                    provider_write_gate = build_provider_write_gate(
                        provider_availability=provider_availability,
                        provider_preference=args.provider_preference,
                        directml_probe=directml_provider_probe,
                        write_requested=write_requested,
                        write_confirmed=write_confirmed,
                        write_preconditions_passed=bool(write_preconditions.get("passed")),
                    )
                    if provider_write_gate.get("passed"):
                        ai_write_concurrency_recheck = job_concurrency_recheck(db, stage="before_ai_write")
                        write_window_protection = build_write_window_protection(
                            write_requested=write_requested,
                        write_confirmed=write_confirmed,
                        write_preconditions_passed=bool(write_preconditions.get("passed")),
                        import_recheck=import_concurrency_recheck,
                        ai_write_recheck=ai_write_concurrency_recheck,
                        operator_lock=operator_lock_state,
                    )
                        if bool(ai_write_concurrency_recheck.get("passed")):
                            with pilot.temporary_env({"AI_TAGGING_ALLOW_PROVIDER_FALLBACK": "false"}):
                                directml_ai_tagging, touched_tags = pilot.run_ai_tagging_pass(
                                    db,
                                    label="directml_primary",
                                    media_ids=downstream_media_ids,
                                    dry_run=False,
                                    provider_preference=DIRECTML_WRITE_PROVIDER_PREFERENCE,
                                    max_items=max_items,
                                    local_files_only=local_files_only,
                                )
                            directml_ai_tagging = _augment_ai_result(directml_ai_tagging)
                        else:
                            provider_write_gate = dict(provider_write_gate)
                            blockers = list(provider_write_gate.get("blockers") or [])
                            blockers.append("concurrent_job_active_before_ai_write")
                            provider_write_gate.update({"passed": False, "write_allowed": False, "blockers": blockers})
                            directml_ai_tagging = _not_run_ai_result(
                                "not_run_write_window_concurrency_blocked",
                                provider_preference=DIRECTML_WRITE_PROVIDER_PREFERENCE,
                                selected_media_count=len(downstream_media_ids),
                            )
                            directml_ai_tagging["write_gate_blockers"] = blockers
                    else:
                        directml_ai_tagging = _not_run_ai_result(
                            "not_run_provider_write_gate_blocked",
                            provider_preference=DIRECTML_WRITE_PROVIDER_PREFERENCE,
                            selected_media_count=len(downstream_media_ids),
                        )
                        directml_ai_tagging["write_gate_blockers"] = list(
                            provider_write_gate.get("blockers") or []
                        )
                else:
                    directml_ai_tagging, touched_tags = pilot.run_ai_tagging_pass(
                        db,
                        label="directml_primary",
                        media_ids=downstream_media_ids,
                        dry_run=True,
                        provider_preference=args.provider_preference,
                        max_items=max_items,
                        local_files_only=local_files_only,
                    )
                    directml_ai_tagging = _augment_ai_result(directml_ai_tagging)
                    provider_write_gate = build_provider_write_gate(
                        provider_availability=provider_availability,
                        provider_preference=args.provider_preference,
                        directml_probe=directml_provider_probe,
                        write_requested=write_requested,
                        write_confirmed=write_confirmed,
                        write_preconditions_passed=bool(write_preconditions.get("passed")),
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
                cpu_fallback = _augment_ai_result(cpu_fallback)
            else:
                status = (
                    "not_run_write_preconditions_blocked"
                    if write_requested and not write_preconditions.get("passed")
                    else ("not_run_no_media" if not downstream_media_ids else "not_run_model_cache_unavailable")
                )
                directml_ai_tagging = _not_run_ai_result(
                    status,
                    provider_preference=args.provider_preference,
                    selected_media_count=len(downstream_media_ids),
                )
                directml_provider_probe = _not_run_ai_result(
                    status,
                    label="directml_prewrite_probe",
                    provider_preference=args.provider_preference,
                    selected_media_count=min(1, len(downstream_media_ids)),
                )
                provider_write_gate = build_provider_write_gate(
                    provider_availability=provider_availability,
                    provider_preference=args.provider_preference,
                    directml_probe=directml_provider_probe,
                    write_requested=write_requested,
                    write_confirmed=write_confirmed,
                    write_preconditions_passed=bool(write_preconditions.get("passed")),
                )
                cpu_fallback = _not_run_ai_result(
                    status,
                    label="cpu_fallback_dry_run",
                    provider_preference=CPU_PROVIDER_PREFERENCE,
                    selected_media_count=min(1, len(downstream_media_ids)),
                )
            localization = validate_localization(db, touched_tags)
        finally:
            db.close()

    import_executed = bool(import_reuse.get("executed"))
    ai_write_executed = bool(directml_ai_tagging.get("executed")) and not bool(directml_ai_tagging.get("dry_run"))
    job_concurrency = dict(job_concurrency)
    job_concurrency.update(
        {
            "write_window_rechecked": bool(write_window_protection.get("write_window_rechecked")),
            "write_window_protection_mode": write_window_protection.get("mode"),
            "durable_lock_held": bool(write_window_protection.get("durable_lock_held")),
            "lock_scope": write_window_protection.get("lock_scope"),
            "operator_sync_lock_acquisition_attempted": bool(operator_lock_state.get("acquisition_attempted")),
            "operator_sync_lock_acquired": bool(operator_lock_state.get("acquired")),
            "operator_sync_lock": operator_lock_state,
            "concurrency_claim_scope": "durable_operator_lock_plus_immediate_rechecks",
        }
    )
    summary: dict[str, Any] = {
        "phase": PHASE,
        "title": "Bounded Operator Sync Scale-Up and Disabled Unattended Sync Design",
        "generated_at": utc_now_iso(),
        "started_at": started_at,
        "pipeline_contract": {
            "contract_id": CONTRACT_ID,
            "status": "pending",
            "claims": {"target_met": False, "safe_to_merge": False, "full_chain_complete": False},
        },
        "run_configuration": {
            "mode": "execute" if write_requested else "dry_run",
            "input_mode": "input_path",
            "max_items": max_items,
            "max_items_cap": MAX_ALLOWED_ITEMS,
            "provider_preference_requested": provider_list(args.provider_preference),
            "actual_write_provider_preference": provider_list(DIRECTML_WRITE_PROVIDER_PREFERENCE),
            "cpu_fallback_provider_preference": [CPU_PROVIDER_PREFERENCE],
            "cpu_fallback_write_allowed": False,
            "provider_fallback_disabled_for_actual_write": True,
            "local_files_only": local_files_only,
            "model_download_allowed": not local_files_only,
            "write_requested": write_requested,
            "operator_confirmation_exact": write_confirmed,
            "required_confirmation_string_public": "exact_s3a_prod2_operator_confirmation",
            "operator_triggered_only": True,
            "s3a_production_automation_enabled": False,
            "unattended_s3b_enabled": bool(
                s3b_scaffold.get("policy", {}).get("unattended_enabled")
                if isinstance(s3b_scaffold.get("policy", {}), Mapping)
                else False
            ),
            "scheduled_s3b_enabled": bool(
                s3b_scaffold.get("policy", {}).get("scheduled_enabled")
                if isinstance(s3b_scaffold.get("policy", {}), Mapping)
                else False
            ),
            "no_full_library_fallback": True,
            "min_stable_age_seconds": min_stable_age_seconds,
            "stability_wait_seconds": stability_wait_seconds,
        },
        "scope": preflight.scope,
        "protected_input_gate": preflight.scope.get("protected_input_gate"),
        "source_file_preflight": preflight.source_file_preflight,
        "model_cache": model_cache,
        "provider_availability": provider_availability,
        "job_concurrency": job_concurrency,
        "write_preconditions": write_preconditions,
        "write_provider_policy": write_provider_policy,
        "write_window_protection": write_window_protection,
        "import_reuse": import_reuse,
        "classification": classification,
        "directml_provider_probe": directml_provider_probe,
        "provider_write_gate": provider_write_gate,
        "directml_ai_tagging": directml_ai_tagging,
        "primary_provider_validation": directml_ai_tagging,
        "cpu_fallback_validation": cpu_fallback,
        "localization": localization,
        "load_control_observations": pilot.load_control_observations(directml_ai_tagging, cpu_fallback),
        "failure_budget": {"passed": False},
        "s3a_boundary": build_s3a_boundary(import_executed, ai_write_executed),
        "s3b_disabled_scaffold": s3b_scaffold,
        "forbidden_operations": {
            "provider_pixiv_gallery_dl_saucenao_google": False,
            "sourceconcept_r1_r2_r1r": False,
            "entity_bridge": False,
            "cleanup_delete_reset_drop_truncate": False,
            "desired_media_backfill": False,
            "scheduled_automation": False,
            "unattended_automation": False,
            "full_library_import_or_tagging": False,
            "source_mutation": False,
        },
        "public_reports": {
            "summary_json_path": repo_relative(SUMMARY_PATH),
            "markdown_report_path": repo_relative(MARKDOWN_PATH),
            "path_style": "repo_relative_public_artifacts",
        },
        "artifact_lifecycle": {
            "artifacts": [
                {
                    "path": "backend/app/services/s3b_unattended_sync_policy.py",
                    "classification": "durable production code",
                    "committed": True,
                },
                {
                    "path": "scripts/run_s3a_prod2_bounded_operator_sync_scaleup.py",
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
            "runner_command": "python scripts/run_s3a_prod2_bounded_operator_sync_scaleup.py",
            "dry_run_or_preflight_completed": True,
            "write_completed": write_requested and write_confirmed and import_executed and ai_write_executed,
            "s3b_scaffold_reported": s3b_scaffold.get("status") == "disabled_scaffold_ready",
        },
        "backlog": [
            "Future S3A ledger/watermark production design should promote item-level cursor state before unattended scheduling.",
            "S3B unattended and scheduled automation remain disabled and must require a later explicit approval phase.",
        ],
    }
    apply_pipeline_status(summary, derive_status(summary))
    summary["failure_budget"] = build_failure_budget(summary)
    summary["safety"] = build_safety(summary)
    try:
        reports_written = write_reports(summary)
        final_status = str(summary.get("pipeline_contract", {}).get("status") or "")
        return 0 if reports_written and final_status in TARGET_STATUSES else 2
    finally:
        operator_lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
