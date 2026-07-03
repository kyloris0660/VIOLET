"""Canonical lifecycle and WorkItem interpretation for manual sync.

The classifier is intentionally pure: callers provide DB/media/app-storage
evidence, and this module turns the evidence into an execution boundary.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping, Optional


class LifecycleKind(str, Enum):
    APP_MEDIA_FOLLOWUP = "APP_MEDIA_FOLLOWUP"
    IMPORT_CANDIDATE = "IMPORT_CANDIDATE"
    RETRYABLE_SOURCE_FAILURE = "RETRYABLE_SOURCE_FAILURE"
    PLACEHOLDER_DEFERRED = "PLACEHOLDER_DEFERRED"
    STABLE_NOOP = "STABLE_NOOP"
    HISTORICAL_DIAGNOSTIC = "HISTORICAL_DIAGNOSTIC"
    CONTINUATION = "CONTINUATION"
    BROKEN_STATE = "BROKEN_STATE"
    FATAL_BLOCKER = "FATAL_BLOCKER"


class WorkItemKind(str, Enum):
    FOLLOWUP = "FOLLOWUP"
    IMPORT = "IMPORT"
    RETRY_SOURCE = "RETRY_SOURCE"
    PLACEHOLDER = "PLACEHOLDER"
    NOOP_DIAGNOSTIC = "NOOP_DIAGNOSTIC"
    BROKEN_STATE = "BROKEN_STATE"


RETRYABLE_SOURCE_FAILURE_REASONS = frozenset(
    {
        "cloud_hydration_failed",
        "cloud_network_unavailable",
        "icloud_placeholder",
        "permission_denied",
        "read_error",
        "read_timeout",
        "source_missing",
    }
)
RETRYABLE_SOURCE_READ_REASONS = frozenset(
    {
        "cloud_hydration_failed",
        "cloud_network_unavailable",
        "icloud_placeholder",
        "permission_denied",
        "read_error",
        "read_timeout",
    }
)
PLACEHOLDER_REASONS = frozenset({"cloud_placeholder", "icloud_placeholder", "cloud_hydration_failed"})
CONTINUATION_REASONS = frozenset({"not_processed_budget_stop", "not_processed_cancelled"})
STABLE_NOOP_REASONS = frozenset(
    {
        "duplicate_hash",
        "existing_media_hash",
        "hidden",
        "skipped_duplicate",
        "skipped_existing_media",
        "unsupported_extension",
        "zero_byte",
        "zero_byte_file",
    }
)

CLASSIFICATION_COMPLETE_STATUSES = frozenset({"classified", "classified_reused"})
AI_TAGGING_COMPLETE_STATUSES = frozenset(
    {
        "ai_tagged",
        "tagged",
        "tagged_reused",
        "ai_tagging_skipped_non_target",
        "skipped_non_target",
    }
)
LOCALIZATION_COMPLETE_STATUSES = frozenset(
    {
        "localized",
        "completed",
        "skipped_no_localizable_tags",
        "skipped_no_new_tags",
        "skipped_static_coverage",
        "localization_not_applicable_non_target",
    }
)


@dataclass(frozen=True)
class LifecycleDecision:
    kind: LifecycleKind
    work_item_kind: WorkItemKind
    reason_code: str
    operator_label: str
    operator_label_zh: str
    is_actionable: bool
    is_visible_in_normal_ui: bool
    consumes_actionable_cap: bool
    can_execute: bool
    allowed_source_reads: bool
    allowed_db_writes: tuple[str, ...]
    terminal_after_execute: bool
    requires_operator_attention: bool
    validator_severity: str
    report_bucket: str
    attempted_in_run: bool = False
    current_downstream_complete: bool = False
    attempted_but_current_incomplete: bool = False
    not_processed_continuation: bool = False
    stable_noop: bool = False
    broken_state: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        payload["work_item_kind"] = self.work_item_kind.value
        payload["allowed_db_writes"] = list(self.allowed_db_writes)
        return payload


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int_or_none(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _truthy_count(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def source_item_reason(item: Any, run_item: Any | None = None) -> str:
    if run_item is not None:
        reason = _text(_value(run_item, "reason"))
        if reason:
            return reason
    return _text(_value(item, "deferred_reason") or _value(item, "failure_reason"))


def source_item_downstream_flags(item: Any) -> dict[str, bool]:
    classification = _text(_value(item, "classification_status"))
    ai_tagging = _text(_value(item, "ai_tagging_status"))
    localization = _text(_value(item, "localization_status"))
    return {
        "classification_done": classification in CLASSIFICATION_COMPLETE_STATUSES,
        "ai_tagging_done": ai_tagging in AI_TAGGING_COMPLETE_STATUSES,
        "localization_done": localization in LOCALIZATION_COMPLETE_STATUSES,
    }


def source_item_downstream_complete(item: Any) -> bool:
    flags = source_item_downstream_flags(item)
    return bool(flags["classification_done"] and flags["ai_tagging_done"] and flags["localization_done"])


def _pending_media_backed_noop(item: Any) -> bool:
    if _int_or_none(_value(item, "media_id")) is None:
        return False
    if _text(_value(item, "import_status")) != "pending":
        return False
    if _text(_value(item, "sync_state")) not in {"new", "changed", "pending"}:
        return False
    if not _text(_value(item, "content_hash")):
        return False
    if source_item_reason(item):
        return False
    return (
        _text(_value(item, "classification_status")) in {"", "waiting_import"}
        and _text(_value(item, "ai_tagging_status")) in {"", "waiting_import"}
        and _text(_value(item, "localization_status")) in {"", "waiting_import", "waiting_ai_tags"}
    )


def source_item_is_media_backed(item: Any) -> bool:
    if _int_or_none(_value(item, "media_id")) is None:
        return False
    if _pending_media_backed_noop(item):
        return False
    import_status = _text(_value(item, "import_status"))
    sync_state = _text(_value(item, "sync_state"))
    reason = source_item_reason(item)
    return (
        import_status in {"imported", "failed", "deferred"}
        or sync_state in {"imported", "downstream_followup_planned", "failed", "deferred"}
        or reason in {"existing_media_hash", "duplicate_hash", "downstream_followup", "not_processed_budget_stop", "source_missing"}
    )


def source_item_requires_app_media_followup(item: Any) -> bool:
    return bool(source_item_is_media_backed(item) and not source_item_downstream_complete(item))


def _decision(
    kind: LifecycleKind,
    *,
    reason_code: str,
    evidence: dict[str, Any],
    attempted_in_run: bool = False,
    current_downstream_complete: bool = False,
    attempted_but_current_incomplete: bool = False,
    not_processed_continuation: bool = False,
) -> LifecycleDecision:
    if kind == LifecycleKind.APP_MEDIA_FOLLOWUP:
        return LifecycleDecision(
            kind=kind,
            work_item_kind=WorkItemKind.FOLLOWUP,
            reason_code=reason_code or "downstream_followup_required",
            operator_label="App-media follow-up",
            operator_label_zh="应用媒体后续处理",
            is_actionable=True,
            is_visible_in_normal_ui=True,
            consumes_actionable_cap=True,
            can_execute=True,
            allowed_source_reads=False,
            allowed_db_writes=("classification_status", "ai_tagging_status", "localization_status", "run_item"),
            terminal_after_execute=True,
            requires_operator_attention=False,
            validator_severity="info",
            report_bucket="app_media_followup",
            attempted_in_run=attempted_in_run,
            current_downstream_complete=current_downstream_complete,
            attempted_but_current_incomplete=attempted_but_current_incomplete,
            evidence=evidence,
        )
    if kind == LifecycleKind.IMPORT_CANDIDATE:
        return LifecycleDecision(
            kind=kind,
            work_item_kind=WorkItemKind.IMPORT,
            reason_code=reason_code or "import_candidate",
            operator_label="Import candidate",
            operator_label_zh="导入候选",
            is_actionable=True,
            is_visible_in_normal_ui=True,
            consumes_actionable_cap=True,
            can_execute=True,
            allowed_source_reads=True,
            allowed_db_writes=("source_item", "media", "run_item", "stage_status"),
            terminal_after_execute=True,
            requires_operator_attention=False,
            validator_severity="info",
            report_bucket="import_candidate",
            attempted_in_run=attempted_in_run,
            current_downstream_complete=current_downstream_complete,
            attempted_but_current_incomplete=attempted_but_current_incomplete,
            evidence=evidence,
        )
    if kind == LifecycleKind.RETRYABLE_SOURCE_FAILURE:
        return LifecycleDecision(
            kind=kind,
            work_item_kind=WorkItemKind.RETRY_SOURCE,
            reason_code=reason_code or "retryable_source_failure",
            operator_label="Retry source",
            operator_label_zh="重试源文件",
            is_actionable=True,
            is_visible_in_normal_ui=True,
            consumes_actionable_cap=True,
            can_execute=True,
            allowed_source_reads=True,
            allowed_db_writes=("retry_metadata", "source_item", "run_item"),
            terminal_after_execute=True,
            requires_operator_attention=False,
            validator_severity="warning",
            report_bucket="retryable_source_failure",
            attempted_in_run=attempted_in_run,
            current_downstream_complete=current_downstream_complete,
            attempted_but_current_incomplete=attempted_but_current_incomplete,
            evidence=evidence,
        )
    if kind == LifecycleKind.PLACEHOLDER_DEFERRED:
        return LifecycleDecision(
            kind=kind,
            work_item_kind=WorkItemKind.PLACEHOLDER,
            reason_code=reason_code or "placeholder_deferred",
            operator_label="Placeholder deferred",
            operator_label_zh="占位文件暂缓",
            is_actionable=False,
            is_visible_in_normal_ui=True,
            consumes_actionable_cap=False,
            can_execute=False,
            allowed_source_reads=False,
            allowed_db_writes=("diagnostic_metadata",),
            terminal_after_execute=False,
            requires_operator_attention=True,
            validator_severity="warning",
            report_bucket="placeholder_deferred",
            attempted_in_run=attempted_in_run,
            current_downstream_complete=current_downstream_complete,
            evidence=evidence,
        )
    if kind == LifecycleKind.CONTINUATION:
        return LifecycleDecision(
            kind=kind,
            work_item_kind=WorkItemKind.IMPORT,
            reason_code=reason_code or "not_processed_budget_stop",
            operator_label="Continuation",
            operator_label_zh="续跑项目",
            is_actionable=True,
            is_visible_in_normal_ui=True,
            consumes_actionable_cap=True,
            can_execute=True,
            allowed_source_reads=True,
            allowed_db_writes=("source_item", "run_item"),
            terminal_after_execute=False,
            requires_operator_attention=False,
            validator_severity="info",
            report_bucket="continuation",
            attempted_in_run=attempted_in_run,
            current_downstream_complete=current_downstream_complete,
            not_processed_continuation=True,
            evidence=evidence,
        )
    if kind == LifecycleKind.BROKEN_STATE:
        return LifecycleDecision(
            kind=kind,
            work_item_kind=WorkItemKind.BROKEN_STATE,
            reason_code=reason_code or "broken_state",
            operator_label="Broken state",
            operator_label_zh="状态异常",
            is_actionable=False,
            is_visible_in_normal_ui=True,
            consumes_actionable_cap=False,
            can_execute=False,
            allowed_source_reads=False,
            allowed_db_writes=(),
            terminal_after_execute=False,
            requires_operator_attention=True,
            validator_severity="error",
            report_bucket="broken_state",
            attempted_in_run=attempted_in_run,
            current_downstream_complete=current_downstream_complete,
            attempted_but_current_incomplete=attempted_but_current_incomplete,
            broken_state=True,
            evidence=evidence,
        )
    if kind == LifecycleKind.FATAL_BLOCKER:
        return LifecycleDecision(
            kind=kind,
            work_item_kind=WorkItemKind.BROKEN_STATE,
            reason_code=reason_code or "fatal_blocker",
            operator_label="Fatal blocker",
            operator_label_zh="致命阻断",
            is_actionable=False,
            is_visible_in_normal_ui=True,
            consumes_actionable_cap=False,
            can_execute=False,
            allowed_source_reads=False,
            allowed_db_writes=(),
            terminal_after_execute=False,
            requires_operator_attention=True,
            validator_severity="fatal",
            report_bucket="fatal_blocker",
            attempted_in_run=attempted_in_run,
            current_downstream_complete=current_downstream_complete,
            broken_state=True,
            evidence=evidence,
        )
    stable = kind == LifecycleKind.STABLE_NOOP
    return LifecycleDecision(
        kind=kind,
        work_item_kind=WorkItemKind.NOOP_DIAGNOSTIC,
        reason_code=reason_code or ("stable_noop" if stable else "historical_diagnostic"),
        operator_label="Stable no-op" if stable else "Historical diagnostic",
        operator_label_zh="稳定无操作" if stable else "历史诊断",
        is_actionable=False,
        is_visible_in_normal_ui=False,
        consumes_actionable_cap=False,
        can_execute=False,
        allowed_source_reads=False,
        allowed_db_writes=(),
        terminal_after_execute=True,
        requires_operator_attention=False,
        validator_severity="info",
        report_bucket="stable_noop" if stable else "historical_diagnostic",
        attempted_in_run=attempted_in_run,
        current_downstream_complete=current_downstream_complete,
        stable_noop=stable,
        evidence=evidence,
    )


def classify_source_item(
    item: Any,
    *,
    media: Any | Mapping[str, Any] | None = None,
    media_lookup_performed: bool = False,
    app_media_exists: Optional[bool] = None,
    current_priority: bool = False,
    attempted_in_run: bool = False,
    run_item: Any | None = None,
) -> LifecycleDecision:
    """Classify a DynamicSourceItem-like object into canonical lifecycle state."""

    import_status = _text(_value(item, "import_status"))
    sync_state = _text(_value(item, "sync_state"))
    reason = source_item_reason(item, run_item)
    media_id = _int_or_none(_value(item, "media_id"))
    has_media = media_id is not None
    media_row_present = media is not None
    current_complete = bool(has_media and source_item_downstream_complete(item))
    media_backed = source_item_is_media_backed(item)
    followup_needed = bool(media_backed and not current_complete)
    run_state = _text(_value(run_item, "item_state")) if run_item is not None else ""
    run_action = _text(_value(run_item, "action")) if run_item is not None else ""
    attempted = bool(
        attempted_in_run
        or run_action in {"import", "downstream_followup", "skip"}
        or run_state in {"imported", "failed", "downstream_followup_planned", "skipped_existing_media"}
    )
    evidence = {
        "source_item_id": _int_or_none(_value(item, "id")),
        "media_id": media_id,
        "import_status": import_status,
        "sync_state": sync_state,
        "reason": reason,
        "has_media": has_media,
        "media_lookup_performed": media_lookup_performed,
        "media_row_present": media_row_present,
        "app_media_exists": app_media_exists,
        "current_downstream_complete": current_complete,
        "media_backed": media_backed,
        "followup_needed": followup_needed,
        "current_priority": current_priority,
        "run_item_state": run_state,
        "run_item_action": run_action,
    }
    attempted_but_current_incomplete = bool(attempted and has_media and not current_complete)

    if has_media and media_lookup_performed and not media_row_present:
        return _decision(
            LifecycleKind.BROKEN_STATE,
            reason_code="media_row_missing",
            evidence=evidence,
            attempted_in_run=attempted,
            current_downstream_complete=current_complete,
            attempted_but_current_incomplete=attempted_but_current_incomplete,
        )
    if media_backed and app_media_exists is False:
        return _decision(
            LifecycleKind.BROKEN_STATE,
            reason_code="app_media_missing",
            evidence=evidence,
            attempted_in_run=attempted,
            current_downstream_complete=current_complete,
            attempted_but_current_incomplete=attempted_but_current_incomplete,
        )
    if followup_needed:
        return _decision(
            LifecycleKind.APP_MEDIA_FOLLOWUP,
            reason_code=reason or "downstream_incomplete",
            evidence=evidence,
            attempted_in_run=attempted,
            current_downstream_complete=current_complete,
            attempted_but_current_incomplete=attempted_but_current_incomplete,
        )
    if sync_state == "deferred_unprocessed" or reason in CONTINUATION_REASONS:
        return _decision(
            LifecycleKind.CONTINUATION,
            reason_code=reason or "not_processed_budget_stop",
            evidence=evidence,
            attempted_in_run=attempted,
            current_downstream_complete=current_complete,
            not_processed_continuation=True,
        )
    if sync_state == "skipped_placeholder" or reason in PLACEHOLDER_REASONS:
        return _decision(
            LifecycleKind.PLACEHOLDER_DEFERRED,
            reason_code=reason or "placeholder_deferred",
            evidence=evidence,
            attempted_in_run=attempted,
            current_downstream_complete=current_complete,
        )
    if import_status in {"failed", "deferred"} and reason in RETRYABLE_SOURCE_FAILURE_REASONS:
        return _decision(
            LifecycleKind.RETRYABLE_SOURCE_FAILURE,
            reason_code=reason,
            evidence=evidence,
            attempted_in_run=attempted,
            current_downstream_complete=current_complete,
        )
    if import_status == "pending" and current_priority:
        return _decision(
            LifecycleKind.IMPORT_CANDIDATE,
            reason_code=reason or "pending_import",
            evidence=evidence,
            attempted_in_run=attempted,
            current_downstream_complete=current_complete,
        )
    if current_complete or sync_state in {"unchanged", "skipped_existing_media", "skipped_duplicate"}:
        return _decision(
            LifecycleKind.STABLE_NOOP,
            reason_code=reason or sync_state or "downstream_complete",
            evidence=evidence,
            attempted_in_run=attempted,
            current_downstream_complete=current_complete,
        )
    if reason in STABLE_NOOP_REASONS or import_status == "skipped":
        return _decision(
            LifecycleKind.STABLE_NOOP,
            reason_code=reason or import_status,
            evidence=evidence,
            attempted_in_run=attempted,
            current_downstream_complete=current_complete,
        )
    return _decision(
        LifecycleKind.HISTORICAL_DIAGNOSTIC,
        reason_code=reason or sync_state or import_status or "historical_diagnostic",
        evidence=evidence,
        attempted_in_run=attempted,
        current_downstream_complete=current_complete,
    )


def classify_plan_item_state(
    *,
    state: str,
    reason: str | None = None,
    media_id: int | None = None,
) -> LifecycleDecision:
    state_value = _text(state)
    reason_code = _text(reason)
    evidence = {"state": state_value, "reason": reason_code, "media_id": media_id}
    if state_value == "downstream_followup_planned":
        return _decision(
            LifecycleKind.APP_MEDIA_FOLLOWUP,
            reason_code=reason_code or "downstream_followup",
            evidence=evidence,
            attempted_in_run=True,
        )
    if state_value == "import_planned":
        return _decision(LifecycleKind.IMPORT_CANDIDATE, reason_code=reason_code or "import_planned", evidence=evidence)
    if state_value == "deferred_unprocessed" or reason_code in CONTINUATION_REASONS:
        return _decision(LifecycleKind.CONTINUATION, reason_code=reason_code or "not_processed_budget_stop", evidence=evidence)
    if state_value == "skipped_placeholder" or reason_code in PLACEHOLDER_REASONS:
        return _decision(LifecycleKind.PLACEHOLDER_DEFERRED, reason_code=reason_code or "placeholder_deferred", evidence=evidence)
    if state_value in {"failed"} and reason_code in RETRYABLE_SOURCE_FAILURE_REASONS:
        return _decision(LifecycleKind.RETRYABLE_SOURCE_FAILURE, reason_code=reason_code, evidence=evidence)
    if state_value in {"skipped_existing_media", "skipped_duplicate", "skipped_unsupported", "unchanged"}:
        return _decision(LifecycleKind.STABLE_NOOP, reason_code=reason_code or state_value, evidence=evidence)
    return _decision(LifecycleKind.HISTORICAL_DIAGNOSTIC, reason_code=reason_code or state_value, evidence=evidence)


def map_manual_sync_operator_status(
    *,
    run_status: str | None,
    outcome_counts: Mapping[str, Any] | None = None,
    retryable_source_failure_count: int | None = None,
    unprocessed_count: int = 0,
    unprocessed_import_planned_count: int = 0,
    downstream_incomplete_count: int = 0,
    localization_incomplete: bool = False,
    stopped_by: str | None = None,
    import_stopped_by: str | None = None,
    preflight_blocked: bool = False,
    fatal_blocker: bool = False,
) -> str:
    """Map legacy/manual-sync run fields to the operator status vocabulary."""

    status = _text(run_status)
    counts = outcome_counts or {}
    retryable = (
        _truthy_count(retryable_source_failure_count)
        if retryable_source_failure_count is not None
        else sum(_truthy_count(counts.get(reason)) for reason in RETRYABLE_SOURCE_FAILURE_REASONS)
    )
    continuation = max(_truthy_count(unprocessed_count), _truthy_count(unprocessed_import_planned_count))
    followup_required = bool(
        downstream_incomplete_count
        or localization_incomplete
        or _truthy_count(counts.get("classification_failed"))
        or _truthy_count(counts.get("ai_tagging_failed"))
        or _truthy_count(counts.get("localization_failed"))
        or status == "completed_with_followup_required"
    )
    stopped = _text(stopped_by or import_stopped_by)

    if preflight_blocked or status == "blocked_preflight":
        return "blocked_preflight"
    if fatal_blocker or status == "failed_systemic":
        return "failed_systemic"
    if status in {"cancelled", "cancelling"} or stopped == "cancelled":
        return "cancelled"
    if followup_required:
        return "completed_with_followup_required"
    if status == "failed" and continuation and stopped not in {"stopped_by_failure_budget", "stopped_by_duration_budget"}:
        return "failed_systemic"
    if continuation and retryable:
        return "completed_with_retryable_failures_plus_continuation"
    if continuation:
        return "completed_with_continuation"
    if retryable:
        return "completed_with_retryable_failures"
    if status in {"completed", "completed_with_failures"}:
        failed = _truthy_count(counts.get("failed"))
        if failed and failed > retryable:
            return "failed_systemic"
        return "completed"
    if status == "failed" and stopped in {"stopped_by_failure_budget", "stopped_by_duration_budget"}:
        return "completed_with_continuation" if continuation else "completed_with_retryable_failures"
    if status == "failed":
        return "failed_systemic"
    return status or "failed_systemic"


def resolve_app_storage_path(storage_root: str | Path, stored_path: str) -> Optional[Path]:
    if not stored_path:
        return None
    raw = str(stored_path)
    if raw.startswith("\\\\") or raw.startswith("//"):
        return None
    normalized = raw.replace("\\", "/")
    if normalized.startswith("/"):
        return None
    if len(normalized) >= 2 and normalized[1] == ":" and normalized[0].isalpha():
        return None
    if PureWindowsPath(raw).is_absolute():
        return None
    try:
        probe = Path(normalized)
        if probe.is_absolute() or ".." in probe.parts:
            return None
        storage_resolved = Path(storage_root).expanduser().resolve(strict=False)
        resolved = (storage_resolved / probe).resolve(strict=False)
        try:
            resolved.relative_to(storage_resolved)
        except ValueError:
            return None
        return resolved
    except (OSError, RuntimeError, ValueError):
        return None


def app_media_exists(media: Any | Mapping[str, Any] | None, *, storage_root: str | Path) -> bool:
    if media is None:
        return False
    path = _text(_value(media, "path"))
    resolved = resolve_app_storage_path(storage_root, path)
    return bool(resolved and resolved.exists())
