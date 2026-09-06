"""Bounded scheduling and recoverable source dispositions on the existing ledger."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone

POLICY_VERSION = 1
REPEAT_FAILURE_LIMIT = 3


def file_version(metadata):
    return {key: metadata.get(key) for key in ("file_size", "mtime_ns")}


def recovery(item):
    return dict((getattr(item, "metadata_json", None) or {}).get("manual_sync_recovery") or {})


def as_utc(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def disposition(item, metadata=None, *, now=None):
    state = recovery(item)
    status = state.get("disposition", "retryable")
    if status == "ignored":
        return status
    if metadata is not None and state.get("file_version") != file_version(metadata):
        return "retryable"
    if status == "terminal" and state.get("policy_version") != POLICY_VERSION:
        return "retryable"
    due = as_utc(state.get("next_attempt_at"))
    if status == "retryable" and due and due > (now or datetime.now(timezone.utc)):
        return "waiting_retry"
    return status


def set_recovery(item, updates):
    metadata = dict(item.metadata_json or {})
    metadata["manual_sync_recovery"] = {**recovery(item), **updates}
    item.metadata_json = metadata


def actual_history(db, item, metadata):
    """Read real outcomes, excluding enqueue/defer rows and guessed counters."""
    from ..models import DynamicSyncRunItem
    rows = db.query(DynamicSyncRunItem).filter(
        DynamicSyncRunItem.source_item_id == item.id,
        DynamicSyncRunItem.action.in_(("import", "retry_source", "attempt")),
    ).order_by(DynamicSyncRunItem.id).all()
    version = file_version(metadata)
    failures, matched, unknown = set(), set(), set()
    latest = None
    for row in rows:
        detail = row.current_metadata_json or {}
        if row.item_state in {"not_executed", "deferred_unprocessed"}:
            continue
        latest = row.created_at
        if row.item_state != "failed":
            continue
        failures.add(row.sync_run_id)
        observed = file_version(detail)
        if any(value is None for value in observed.values()):
            unknown.add(row.sync_run_id)
        elif observed == version:
            matched.add(row.sync_run_id)
    return dict(actual_failure_run_ids=sorted(failures),
        matching_version_failure_run_ids=sorted(matched), unknown_version_failure_run_ids=sorted(unknown),
        last_attempt_at=latest.isoformat() if latest else None)


def start_attempt(item, *, run_id, metadata, now, db=None):
    state = recovery(item)
    version = file_version(metadata)
    changed = state.get("file_version") != version
    ids = [] if changed else state.get("version_failure_run_ids", [])
    history = actual_history(db, item, metadata) if db is not None else state.get("history_evidence", {})
    # An explicit resume grants a fresh bounded chance; do not immediately
    # reapply failures the operator has already reviewed.
    resumed = max((event.get("at", "") for event in state.get("operator_events", [])
                   if event.get("action") == "resume"), default="")
    if not resumed:
        ids = sorted(set(ids) | set(history.get("matching_version_failure_run_ids", [])))
    set_recovery(item, dict(last_attempt_at=now.isoformat(), last_attempt_run_id=run_id,
        file_version=version, policy_version=POLICY_VERSION, disposition="retryable",
        next_attempt_at=None, version_failure_run_ids=ids, history_evidence=history))


def record_failure(item, *, run_id, reason, metadata, now):
    state = recovery(item)
    version = file_version(metadata)
    ids = list(state.get("version_failure_run_ids") or []) if state.get("file_version") == version else []
    if run_id not in ids:
        ids.append(run_id)
    # Only persisted, version-bound real failures count toward deferral.
    # Historical attempt_count is deliberately never used as a failure count.
    deferred = len(ids) >= REPEAT_FAILURE_LIMIT
    diagnostic = metadata.get("private_diagnostic") or {}
    terminal = (diagnostic.get("stage") == "copied_image_decode"
        and diagnostic.get("copied_version_verified") is True
        and diagnostic.get("exception_type") in {"UnidentifiedImageError", "DecompressionBombError"})
    set_recovery(item, dict(disposition="terminal" if terminal else "deferred_diagnosis" if deferred else "retryable",
        reason=reason, file_version=version, policy_version=POLICY_VERSION,
        version_failure_run_ids=ids[-REPEAT_FAILURE_LIMIT:], last_attempt_at=now.isoformat(),
        last_attempt_run_id=run_id,
        next_attempt_at=None if deferred or terminal else (now + timedelta(minutes=5 * len(ids))).isoformat(),
        reentry_condition="file_version_or_support_policy_changed_or_operator_resume" if terminal else
            "file_version_changed_or_operator_resume" if deferred else "retry_time_reached_or_operator_resume"))


def finish_attempt(item, *, now):
    set_recovery(item, dict(disposition="complete", last_success_at=now.isoformat(),
        next_attempt_at=None, version_failure_run_ids=[], reason=None))


def last_attempt(item):
    state = recovery(item)
    retry = (getattr(item, "metadata_json", None) or {}).get("manual_sync_retry") or {}
    return str(state.get("last_attempt_at") or retry.get("last_retry_at") or "")


def fair_order(records, *, cursor=0):
    """4 import / 1 follow-up / 1 retry slots, lending empty slots to work.

    Cursor is persisted at each actual admission, including before I/O. Thus
    cap=1, cancellation and a process restart cannot reset fairness to slot 0.
    Each class rotates by its last actual attempt; unseen items sort first.
    """
    buckets = {key: deque() for key in ("IMPORT", "FOLLOWUP", "RETRY_SOURCE")}
    diagnostics = []
    ordered = sorted(records, key=lambda r: (
        str(r.get("last_attempt_at") or ""),
        int(r.get("candidate_priority", 100)),
        -int(r.get("candidate_mtime_ns") or 0),
        str(r.get("relative_path_hash_full") or r.get("relative_path_hash") or "")))
    for record in ordered:
        kind = str(record.get("work_item_kind") or (record.get("lifecycle_decision") or {}).get("work_item_kind") or "")
        if kind in buckets and record.get("can_execute", True):
            buckets[kind].append(record)
        else:
            diagnostics.append(record)
    slots = ("IMPORT", "FOLLOWUP", "IMPORT", "IMPORT", "IMPORT", "RETRY_SOURCE")
    result = []
    while any(buckets.values()):
        kind = slots[cursor % len(slots)]
        cursor += 1
        if not buckets[kind]:
            continue
        result.append({**buckets[kind].popleft(), "scheduler_cursor_after": cursor})
    return result + diagnostics


def persisted_cursor(db, root_id):
    from ..models import DynamicSyncRun
    for row in db.query(DynamicSyncRun.summary_json).filter(
            DynamicSyncRun.run_type == "manual_sync_execute").order_by(DynamicSyncRun.id.desc()):
        execute = (row[0] or {}).get("manual_sync_execute") or {}
        if ("scheduler_cursor" in execute and
                int((execute.get("request") or {}).get("root_id") or 0) == int(root_id or 0)):
            return int(execute.get("scheduler_cursor") or 0)
    return 0
