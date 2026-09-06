"""Authenticated private item accounting and explicit operator recovery."""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import or_, and_, func
from sqlalchemy.orm import Session

from ...auth import require_admin_mode
from ...database import get_db
from ...models import DynamicSourceItem, DynamicSourceRoot, DynamicSyncRun, DynamicSyncRunItem, User
from ...services.manual_sync_recovery import recovery, disposition, set_recovery, file_version, POLICY_VERSION
from ...services.manual_sync_lifecycle import (CLASSIFICATION_COMPLETE_STATUSES, AI_TAGGING_COMPLETE_STATUSES,
    LOCALIZATION_COMPLETE_STATUSES, source_item_downstream_complete)

router = APIRouter()


@router.get("/dynamic-library-sync/recovery-items")
def recovery_items(response: Response, root_id: int, after_id: int = 0,
                   limit: int = Query(100, ge=1, le=200), db: Session = Depends(get_db),
                   _user: User = Depends(require_admin_mode)):
    response.headers["Cache-Control"] = "no-store"
    root = db.get(DynamicSourceRoot, root_id)
    if root is None or not root.is_active:
        raise HTTPException(404, detail={"code": "source_root_not_found"})
    query = db.query(DynamicSourceItem).filter(DynamicSourceItem.source_root_id == root_id,
        or_(DynamicSourceItem.sync_state.in_(("failed", "deferred", "deferred_unprocessed", "import_in_progress", "skipped_unsupported", "skipped_placeholder")),
            DynamicSourceItem.failure_reason.isnot(None),
            and_(DynamicSourceItem.media_id.isnot(None), DynamicSourceItem.import_status == 'imported',
                or_(func.coalesce(DynamicSourceItem.classification_status,'').notin_(CLASSIFICATION_COMPLETE_STATUSES),
                    func.coalesce(DynamicSourceItem.ai_tagging_status,'').notin_(AI_TAGGING_COMPLETE_STATUSES),
                    func.coalesce(DynamicSourceItem.localization_status,'').notin_(LOCALIZATION_COMPLETE_STATUSES)))))
    total = query.count()
    items = query.filter(DynamicSourceItem.id > after_id).order_by(DynamicSourceItem.id).limit(limit + 1).all()
    result = []
    for item in items[:limit]:
        state = recovery(item)
        attempt = db.query(DynamicSyncRunItem).filter(DynamicSyncRunItem.source_item_id == item.id,
            DynamicSyncRunItem.action.in_(("import", "retry_source", "attempt"))).order_by(DynamicSyncRunItem.id.desc()).first()
        detail = dict(attempt.current_metadata_json or {}) if attempt else {}
        current_disposition = disposition(item)
        if item.media_id and not source_item_downstream_complete(item) and current_disposition in {'complete','retryable','waiting_retry'}:
            current_disposition = 'followup_pending'
        result.append(dict(source_item_id=item.id, relative_path=item.relative_path, media_id=item.media_id,
            sync_state=item.sync_state, import_status=item.import_status,
            classification_status=item.classification_status, ai_tagging_status=item.ai_tagging_status,
            localization_status=item.localization_status, reason=item.failure_reason or item.deferred_reason,
            disposition=current_disposition, recovery=state,
            last_attempt_run_id=attempt.sync_run_id if attempt else None,
            last_attempt_run_item_id=attempt.id if attempt else None,
            diagnostic=detail.get("private_diagnostic"),
            unexecuted=item.sync_state == "deferred_unprocessed"))
    latest = db.query(DynamicSyncRun).filter(DynamicSyncRun.run_type == 'manual_sync_execute',
        DynamicSyncRun.summary_json['manual_sync_execute']['request']['root_id'].as_integer() == root_id
    ).order_by(DynamicSyncRun.id.desc()).first()
    discovery = (((latest.summary_json or {}).get('manual_sync_execute') or {}).get('private_discovery') or {}) if latest else {}
    return dict(items=result, total=total, next_after_id=items[limit-1].id if len(items) > limit else None,
        metadata_dispositions=discovery.get('metadata_dispositions', []) if after_id == 0 else [],
        directory_errors=discovery.get('directory_errors', []) if after_id == 0 else [])


class RecoveryAction(BaseModel):
    action: Literal["resume", "defer", "ignore"]


@router.post("/dynamic-library-sync/recovery-items/{source_item_id}")
def update_recovery(source_item_id: int, body: RecoveryAction, db: Session = Depends(get_db),
                    user: User = Depends(require_admin_mode)):
    item = db.get(DynamicSourceItem, source_item_id)
    if item is None or not item.source_root.is_active:
        raise HTTPException(404, detail={"code": "source_item_not_found"})
    if db.query(DynamicSyncRun.id).filter(DynamicSyncRun.run_type == "manual_sync_execute",
            DynamicSyncRun.status.in_(("pending", "running", "cancelling"))).first():
        raise HTTPException(409, detail={"code": "manual_sync_execute_already_active"})
    state = recovery(item)
    now = datetime.now(timezone.utc).isoformat()
    events = list(state.get("operator_events") or [])
    events.append(dict(action=body.action, at=now, user_id=user.id))
    set_recovery(item, dict(disposition={"resume": "retryable", "defer": "deferred_diagnosis", "ignore": "ignored"}[body.action],
        file_version=file_version(dict(file_size=item.file_size, mtime_ns=item.mtime_ns)),
        policy_version=POLICY_VERSION, next_attempt_at=None,
        version_failure_run_ids=[] if body.action == "resume" else state.get("version_failure_run_ids", []),
        operator_events=events, reentry_condition="operator_resume_or_file_version_changed"))
    if body.action == "resume" and item.media_id is None:
        item.sync_state = "deferred_unprocessed"
        item.import_status = "deferred"
        item.deferred_reason = "not_processed_budget_stop"
    db.commit()
    return dict(source_item_id=item.id, disposition=disposition(item))
