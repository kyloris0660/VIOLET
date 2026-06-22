"""Disabled S3B unattended sync policy scaffold.

This module is intentionally side-effect free: it defines the future sync
vocabulary and conservative source-file gates without starting schedulers,
opening database sessions, or enabling automatic writes.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..config import settings
from ..utils.cloud_files import FILE_ATTRIBUTE_HIDDEN, FILE_ATTRIBUTE_SYSTEM
from .source_ingestion_gate import SourceIngestionGate


SUPPORTED_SYNC_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".gif"}
)
TEMP_SUFFIXES: frozenset[str] = frozenset(
    {".tmp", ".temp", ".partial", ".part", ".crdownload", ".download"}
)


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "1", "yes", "on"}
    return bool(value)


@dataclass(frozen=True)
class SyncPolicy:
    unattended_enabled: bool = False
    scheduled_enabled: bool = False
    max_items: int = 0
    root_count: int = 0
    require_operator_confirmation: bool = True
    dry_run_only: bool = True
    local_files_only: bool = True
    explicit_roots_only: bool = True
    no_full_library_fallback: bool = True
    roots_read_only: bool = True
    no_source_mutation: bool = True
    no_destructive_operations: bool = True
    skip_cloud_placeholders: bool = True
    skip_zero_byte: bool = True
    skip_recent_or_unstable: bool = True
    skip_hidden_temp_system: bool = True
    skip_unsupported: bool = True
    duplicate_hash_reuses_existing: bool = True
    app_storage_writes_after_gates_only: bool = True
    single_active_sync_job: bool = True
    block_concurrent_import_or_tagging: bool = True
    public_redaction_required: bool = True
    private_paths_excluded_from_public_report: bool = True
    min_stable_age_seconds: int = 60
    stability_wait_seconds: float = 0.25

    @classmethod
    def from_settings(cls, settings_obj: Any = settings) -> "SyncPolicy":
        roots = getattr(settings_obj, "S3B_SYNC_SOURCE_ROOTS", [])
        return cls(
            unattended_enabled=_safe_bool(getattr(settings_obj, "S3B_UNATTENDED_SYNC_ENABLED", False)),
            scheduled_enabled=_safe_bool(getattr(settings_obj, "S3B_SCHEDULED_SYNC_ENABLED", False)),
            max_items=max(0, int(getattr(settings_obj, "S3B_SYNC_MAX_ITEMS", 0) or 0)),
            root_count=len(tuple(roots or ())),
            require_operator_confirmation=_safe_bool(
                getattr(settings_obj, "S3B_REQUIRE_OPERATOR_CONFIRMATION", True)
            ),
            dry_run_only=_safe_bool(getattr(settings_obj, "S3B_DRY_RUN_ONLY", True)),
            min_stable_age_seconds=max(
                0, int(getattr(settings_obj, "S3B_SYNC_MIN_STABLE_AGE_SECONDS", 60) or 0)
            ),
            stability_wait_seconds=max(
                0.0, float(getattr(settings_obj, "S3B_SYNC_STABILITY_WAIT_SECONDS", 0.25) or 0.0)
            ),
        )

    def to_public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["roots_redacted"] = True
        payload["settings_status"] = (
            "disabled_by_default"
            if not self.unattended_enabled and not self.scheduled_enabled
            else "enabled_requires_future_approval"
        )
        return payload


@dataclass(frozen=True)
class SyncScope:
    root_count: int = 0
    max_items: int = 0
    explicit_roots_only: bool = True
    no_full_library_fallback: bool = True
    roots_redacted: bool = True

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SyncTrigger:
    trigger_type: str = "operator_manual"
    unattended: bool = False
    scheduled: bool = False
    operator_required: bool = True
    scheduler_started: bool = False
    background_job_started: bool = False

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SyncWatermark:
    identity_strategy: str = "root_identity_hash_plus_relative_path_hash"
    last_scan_timestamp_recorded: bool = False
    file_fingerprint_strategy: str = "size_mtime_ns_optional_content_hash"
    decision_ledger_strategy: str = "per_run_item_decision_rows"
    imported_or_reused_counts_recorded: bool = True
    retry_after_for_failures: bool = True
    run_id_required: bool = True
    trigger_source_recorded: bool = True

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SyncSafetyGate:
    name: str
    passed: bool
    reason: str = "ok"

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SyncDecision:
    status: str
    allowed_to_scan: bool = False
    allowed_to_write: bool = False
    dry_run_only: bool = True
    gates: tuple[SyncSafetyGate, ...] = ()

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "allowed_to_scan": self.allowed_to_scan,
            "allowed_to_write": self.allowed_to_write,
            "dry_run_only": self.dry_run_only,
            "gates": [gate.to_public_dict() for gate in self.gates],
        }


@dataclass(frozen=True)
class SyncRunSummary:
    status: str = "disabled_scaffold_only"
    run_id_required: bool = True
    imported_count: int = 0
    reused_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    source_mutation: bool = False
    app_storage_writes: bool = False
    public_redaction_required: bool = True

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceFileDecision:
    safe_label: str
    eligible: bool
    reason: str
    source_state: str
    size_bytes: int = 0
    supported_extension: bool = False
    stable_size_mtime: bool = False
    recently_modified: bool = False
    cloud_gate: Mapping[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["paths_redacted"] = True
        return payload


def _has_hidden_or_system_attribute(cloud_state: Mapping[str, Any]) -> bool:
    raw = cloud_state.get("attributes_raw")
    try:
        raw_int = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        raw_int = 0
    names = set(cloud_state.get("attribute_names") or ())
    return bool(
        raw_int & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM)
        or "hidden" in names
        or "system" in names
    )


def _looks_temp_or_hidden(path: Path, cloud_state: Mapping[str, Any]) -> bool:
    name = path.name
    lower_name = name.casefold()
    if name.startswith(".") or name.startswith("~$"):
        return True
    if lower_name.endswith(".icloud"):
        return True
    if path.suffix.casefold() in TEMP_SUFFIXES:
        return True
    return _has_hidden_or_system_attribute(cloud_state)


def evaluate_source_file(
    path: str | Path,
    *,
    safe_label: str = "item",
    min_stable_age_seconds: int = 60,
    stability_wait_seconds: float = 0.25,
) -> SourceFileDecision:
    """Conservatively decide whether one explicit source file is selectable."""

    file_path = Path(path)
    gate = SourceIngestionGate.evaluate_path_source(
        file_path,
        safe_label=safe_label,
        hydration_policy_enabled=False,
    )
    public_gate = gate.to_public_dict()
    if gate.blocked:
        return SourceFileDecision(
            safe_label=safe_label,
            eligible=False,
            reason=gate.reason,
            source_state="cloud_or_path_blocked",
            cloud_gate=public_gate,
        )

    cloud_state = public_gate.get("cloud_state") or {}
    if _looks_temp_or_hidden(file_path, cloud_state):
        return SourceFileDecision(
            safe_label=safe_label,
            eligible=False,
            reason="hidden_temp_system_or_placeholder",
            source_state="skipped",
            cloud_gate=public_gate,
        )

    supported = file_path.suffix.casefold() in SUPPORTED_SYNC_EXTENSIONS
    if not supported:
        return SourceFileDecision(
            safe_label=safe_label,
            eligible=False,
            reason="unsupported_extension",
            source_state="skipped",
            supported_extension=False,
            cloud_gate=public_gate,
        )

    try:
        first = file_path.stat()
    except OSError:
        return SourceFileDecision(
            safe_label=safe_label,
            eligible=False,
            reason="unreadable_source",
            source_state="failed",
            supported_extension=supported,
            cloud_gate=public_gate,
        )

    if first.st_size <= 0:
        return SourceFileDecision(
            safe_label=safe_label,
            eligible=False,
            reason="zero_byte_file",
            source_state="skipped",
            supported_extension=supported,
            cloud_gate=public_gate,
        )

    if stability_wait_seconds > 0:
        time.sleep(stability_wait_seconds)
    try:
        second = file_path.stat()
    except OSError:
        return SourceFileDecision(
            safe_label=safe_label,
            eligible=False,
            reason="unreadable_source",
            source_state="failed",
            supported_extension=supported,
            cloud_gate=public_gate,
        )

    stable = (
        first.st_size == second.st_size
        and getattr(first, "st_mtime_ns", int(first.st_mtime * 1_000_000_000))
        == getattr(second, "st_mtime_ns", int(second.st_mtime * 1_000_000_000))
    )
    if not stable:
        return SourceFileDecision(
            safe_label=safe_label,
            eligible=False,
            reason="unstable_size_or_mtime",
            source_state="skipped",
            size_bytes=int(second.st_size),
            supported_extension=supported,
            stable_size_mtime=False,
            cloud_gate=public_gate,
        )

    age_seconds = max(0.0, time.time() - float(second.st_mtime))
    recently_modified = min_stable_age_seconds > 0 and age_seconds < min_stable_age_seconds
    if recently_modified:
        return SourceFileDecision(
            safe_label=safe_label,
            eligible=False,
            reason="recently_modified",
            source_state="skipped",
            size_bytes=int(second.st_size),
            supported_extension=supported,
            stable_size_mtime=True,
            recently_modified=True,
            cloud_gate=public_gate,
        )

    return SourceFileDecision(
        safe_label=safe_label,
        eligible=True,
        reason="local_readable_stable_supported_file",
        source_state="available",
        size_bytes=int(second.st_size),
        supported_extension=supported,
        stable_size_mtime=True,
        recently_modified=False,
        cloud_gate=public_gate,
    )


def build_disabled_decision(policy: SyncPolicy) -> SyncDecision:
    gates = (
        SyncSafetyGate("unattended_disabled", not policy.unattended_enabled),
        SyncSafetyGate("scheduled_disabled", not policy.scheduled_enabled),
        SyncSafetyGate("dry_run_only", policy.dry_run_only),
        SyncSafetyGate("operator_confirmation_required", policy.require_operator_confirmation),
        SyncSafetyGate("explicit_roots_only", policy.explicit_roots_only),
        SyncSafetyGate("no_full_library_fallback", policy.no_full_library_fallback),
        SyncSafetyGate("no_source_mutation", policy.no_source_mutation),
        SyncSafetyGate("no_destructive_operations", policy.no_destructive_operations),
        SyncSafetyGate("single_active_sync_job_policy", policy.single_active_sync_job),
        SyncSafetyGate("block_concurrent_import_or_tagging", policy.block_concurrent_import_or_tagging),
        SyncSafetyGate("private_paths_excluded_from_public_report", policy.private_paths_excluded_from_public_report),
    )
    passed = all(gate.passed for gate in gates)
    return SyncDecision(
        status="disabled_scaffold_ready" if passed else "blocked_policy_enabled",
        allowed_to_scan=False,
        allowed_to_write=False,
        dry_run_only=True,
        gates=gates,
    )


def build_disabled_scaffold(settings_obj: Any = settings) -> dict[str, Any]:
    """Return the public S3B scaffold proof without starting any work."""

    policy = SyncPolicy.from_settings(settings_obj)
    scope = SyncScope(
        root_count=policy.root_count,
        max_items=policy.max_items,
        explicit_roots_only=policy.explicit_roots_only,
        no_full_library_fallback=policy.no_full_library_fallback,
    )
    trigger = SyncTrigger(
        trigger_type="disabled_future_scheduled_or_unattended",
        unattended=policy.unattended_enabled,
        scheduled=policy.scheduled_enabled,
        operator_required=policy.require_operator_confirmation,
        scheduler_started=False,
        background_job_started=False,
    )
    decision = build_disabled_decision(policy)
    return {
        "status": decision.status,
        "policy": policy.to_public_dict(),
        "scope": scope.to_public_dict(),
        "trigger": trigger.to_public_dict(),
        "watermark": SyncWatermark().to_public_dict(),
        "decision": decision.to_public_dict(),
        "run_summary": SyncRunSummary().to_public_dict(),
        "cloud_file_policy": {
            "conservative_fallback": (
                "skip unless local readable, nonzero, stable size and mtime, "
                "supported extension, and no cloud placeholder metadata risk"
            ),
            "skip_reasons": [
                "cloud_offline",
                "cloud_recall_on_open",
                "cloud_recall_on_data_access",
                "cloud_reparse_point",
                "cloud_sparse_file",
                "cloud_hydration_failed",
                "zero_byte_file",
                "unreadable_source",
                "unstable_size_or_mtime",
                "recently_modified",
                "hidden_temp_system_or_placeholder",
                "unsupported_extension",
            ],
            "paths_redacted": True,
        },
        "scheduler_started": False,
        "background_job_started": False,
        "automatic_writes_started": False,
        "source_mutation": False,
        "cleanup_delete_reset_drop_truncate": False,
        "roots_redacted": True,
    }


def public_file_decision_counts(decisions: Iterable[SourceFileDecision]) -> dict[str, Any]:
    rows = [decision.to_public_dict() for decision in decisions]
    by_reason: dict[str, int] = {}
    for row in rows:
        reason = str(row.get("reason") or "unknown")
        by_reason[reason] = by_reason.get(reason, 0) + 1
    return {
        "evaluated_count": len(rows),
        "eligible_count": sum(1 for row in rows if row.get("eligible")),
        "skipped_count": sum(1 for row in rows if not row.get("eligible")),
        "reason_counts": dict(sorted(by_reason.items())),
        "public_item_results": rows,
        "paths_redacted": True,
    }
