from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.manual_sync_lifecycle import (  # noqa: E402
    LifecycleKind,
    WorkItemKind,
    classify_plan_item_state,
    classify_source_item,
    manual_sync_operator_label_catalog,
    map_manual_sync_operator_status,
)


def _item(**overrides: object) -> SimpleNamespace:
    payload = {
        "id": 1,
        "source_root_id": 1,
        "media_id": None,
        "sync_state": "new",
        "source_status": "available",
        "import_status": "pending",
        "classification_status": "waiting_import",
        "ai_tagging_status": "waiting_import",
        "localization_status": "waiting_ai_tags",
        "failure_reason": None,
        "deferred_reason": None,
        "content_hash": None,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _media(**overrides: object) -> SimpleNamespace:
    payload = {"id": 10, "path": "media/original/test.png", "content_class": "anime", "hash": "hash"}
    payload.update(overrides)
    return SimpleNamespace(**payload)


@pytest.mark.parametrize(
    (
        "name",
        "item",
        "media",
        "app_exists",
        "current_priority",
        "expected_kind",
        "expected_work",
        "source_reads",
        "consumes_cap",
        "can_execute",
    ),
    [
        (
            "fully_successful_import",
            _item(
                media_id=10,
                sync_state="imported",
                import_status="imported",
                classification_status="classified",
                ai_tagging_status="ai_tagged",
                localization_status="localized",
            ),
            _media(),
            True,
            False,
            LifecycleKind.STABLE_NOOP,
            WorkItemKind.NOOP_DIAGNOSTIC,
            False,
            False,
            False,
        ),
        (
            "import_plus_classification_failure",
            _item(
                media_id=10,
                sync_state="imported",
                import_status="imported",
                classification_status="failed",
                ai_tagging_status="blocked_classification_not_completed",
                localization_status="blocked_classification_not_completed",
                failure_reason="classification_failed",
            ),
            _media(),
            True,
            False,
            LifecycleKind.APP_MEDIA_FOLLOWUP,
            WorkItemKind.FOLLOWUP,
            False,
            True,
            True,
        ),
        (
            "import_plus_ai_tagging_failure",
            _item(
                media_id=10,
                sync_state="imported",
                import_status="imported",
                classification_status="classified",
                ai_tagging_status="failed_ai_tagger_model_uncached",
                localization_status="blocked_ai_tagging_failed",
                failure_reason="ai_tagger_model_uncached",
            ),
            _media(),
            True,
            False,
            LifecycleKind.APP_MEDIA_FOLLOWUP,
            WorkItemKind.FOLLOWUP,
            False,
            True,
            True,
        ),
        (
            "import_plus_localization_deferred",
            _item(
                media_id=10,
                sync_state="imported",
                import_status="imported",
                classification_status="classified",
                ai_tagging_status="ai_tagged",
                localization_status="deferred",
                deferred_reason="localization_failed",
            ),
            _media(),
            True,
            False,
            LifecycleKind.APP_MEDIA_FOLLOWUP,
            WorkItemKind.FOLLOWUP,
            False,
            True,
            True,
        ),
        (
            "read_error_before_import",
            _item(sync_state="failed", import_status="failed", failure_reason="read_error"),
            None,
            None,
            False,
            LifecycleKind.RETRYABLE_SOURCE_FAILURE,
            WorkItemKind.RETRY_SOURCE,
            True,
            True,
            True,
        ),
        (
            "read_timeout_before_import",
            _item(sync_state="failed", import_status="failed", failure_reason="read_timeout"),
            None,
            None,
            False,
            LifecycleKind.RETRYABLE_SOURCE_FAILURE,
            WorkItemKind.RETRY_SOURCE,
            True,
            True,
            True,
        ),
        (
            "media_backed_read_timeout_retry_source",
            _item(
                media_id=10,
                sync_state="failed",
                import_status="failed",
                classification_status="pending",
                ai_tagging_status="pending",
                localization_status="waiting_ai_tags",
                failure_reason="read_timeout",
            ),
            _media(),
            True,
            False,
            LifecycleKind.RETRYABLE_SOURCE_FAILURE,
            WorkItemKind.RETRY_SOURCE,
            True,
            True,
            True,
        ),
        (
            "media_backed_cloud_hydration_failed_retry_source",
            _item(
                media_id=10,
                sync_state="failed",
                import_status="failed",
                classification_status="pending",
                ai_tagging_status="pending",
                localization_status="waiting_ai_tags",
                failure_reason="cloud_hydration_failed",
            ),
            _media(),
            True,
            False,
            LifecycleKind.RETRYABLE_SOURCE_FAILURE,
            WorkItemKind.RETRY_SOURCE,
            True,
            True,
            True,
        ),
        (
            "failure_budget_stop_continuation",
            _item(sync_state="deferred_unprocessed", import_status="deferred", deferred_reason="not_processed_budget_stop"),
            None,
            None,
            False,
            LifecycleKind.CONTINUATION,
            WorkItemKind.IMPORT,
            True,
            True,
            True,
        ),
        (
            "cap_limited_continuation",
            _item(sync_state="deferred_unprocessed", import_status="pending", deferred_reason="not_processed_budget_stop"),
            None,
            None,
            False,
            LifecycleKind.CONTINUATION,
            WorkItemKind.IMPORT,
            True,
            True,
            True,
        ),
        (
            "downstream_followup_source_missing",
            _item(
                media_id=10,
                sync_state="failed",
                import_status="failed",
                classification_status="pending",
                ai_tagging_status="pending",
                localization_status="waiting_ai_tags",
                failure_reason="source_missing",
            ),
            _media(),
            True,
            False,
            LifecycleKind.APP_MEDIA_FOLLOWUP,
            WorkItemKind.FOLLOWUP,
            False,
            True,
            True,
        ),
        (
            "downstream_followup_app_storage_missing",
            _item(
                media_id=10,
                sync_state="imported",
                import_status="imported",
                classification_status="pending",
                ai_tagging_status="pending",
                localization_status="waiting_ai_tags",
            ),
            _media(),
            False,
            False,
            LifecycleKind.BROKEN_STATE,
            WorkItemKind.BROKEN_STATE,
            False,
            False,
            False,
        ),
        (
            "terminal_media_backed_app_storage_missing",
            _item(
                media_id=10,
                sync_state="imported",
                import_status="imported",
                classification_status="classified",
                ai_tagging_status="ai_tagged",
                localization_status="localized",
            ),
            _media(),
            False,
            False,
            LifecycleKind.BROKEN_STATE,
            WorkItemKind.BROKEN_STATE,
            False,
            False,
            False,
        ),
        (
            "existing_media_duplicate_terminal",
            _item(
                media_id=10,
                sync_state="skipped_existing_media",
                import_status="deferred",
                classification_status="classified",
                ai_tagging_status="ai_tagged",
                localization_status="localized",
                deferred_reason="existing_media_hash",
            ),
            _media(),
            True,
            False,
            LifecycleKind.STABLE_NOOP,
            WorkItemKind.NOOP_DIAGNOSTIC,
            False,
            False,
            False,
        ),
        (
            "existing_media_duplicate_downstream_incomplete",
            _item(
                media_id=10,
                sync_state="skipped_existing_media",
                import_status="deferred",
                classification_status="deferred",
                ai_tagging_status="deferred",
                localization_status="deferred",
                deferred_reason="existing_media_hash",
            ),
            _media(),
            True,
            False,
            LifecycleKind.APP_MEDIA_FOLLOWUP,
            WorkItemKind.FOLLOWUP,
            False,
            True,
            True,
        ),
        (
            "placeholder_cloud_hydration_deferred",
            _item(sync_state="skipped_placeholder", import_status="deferred", deferred_reason="cloud_placeholder"),
            None,
            None,
            False,
            LifecycleKind.PLACEHOLDER_DEFERRED,
            WorkItemKind.PLACEHOLDER,
            False,
            False,
            False,
        ),
        (
            "placeholder_cloud_hydration_failed",
            _item(sync_state="skipped_placeholder", import_status="deferred", deferred_reason="cloud_hydration_failed"),
            None,
            None,
            False,
            LifecycleKind.PLACEHOLDER_DEFERRED,
            WorkItemKind.PLACEHOLDER,
            False,
            False,
            False,
        ),
        (
            "placeholder_icloud_marker",
            _item(sync_state="skipped_placeholder", import_status="deferred", deferred_reason="icloud_placeholder"),
            None,
            None,
            False,
            LifecycleKind.PLACEHOLDER_DEFERRED,
            WorkItemKind.PLACEHOLDER,
            False,
            False,
            False,
        ),
        (
            "stale_legacy_noop",
            _item(sync_state="skipped_existing_media", import_status="deferred", deferred_reason="existing_media_hash"),
            None,
            None,
            False,
            LifecycleKind.STABLE_NOOP,
            WorkItemKind.NOOP_DIAGNOSTIC,
            False,
            False,
            False,
        ),
        (
            "media_backed_downstream_complete_ignores_stale_continuation",
            _item(
                media_id=10,
                sync_state="imported",
                import_status="imported",
                classification_status="classified",
                ai_tagging_status="ai_tagged",
                localization_status="localized",
                deferred_reason="not_processed_budget_stop",
            ),
            _media(),
            True,
            False,
            LifecycleKind.STABLE_NOOP,
            WorkItemKind.NOOP_DIAGNOSTIC,
            False,
            False,
            False,
        ),
        (
            "media_backed_downstream_complete_ignores_stale_retry_reason",
            _item(
                media_id=10,
                sync_state="failed",
                import_status="failed",
                classification_status="classified",
                ai_tagging_status="ai_tagged",
                localization_status="localized",
                failure_reason="read_timeout",
            ),
            _media(),
            True,
            False,
            LifecycleKind.STABLE_NOOP,
            WorkItemKind.NOOP_DIAGNOSTIC,
            False,
            False,
            False,
        ),
        (
            "legacy_unchanged_noop",
            _item(sync_state="unchanged", import_status="deferred", deferred_reason="unchanged"),
            None,
            None,
            False,
            LifecycleKind.STABLE_NOOP,
            WorkItemKind.NOOP_DIAGNOSTIC,
            False,
            False,
            False,
        ),
        (
            "source_missing_media_backed_downstream_incomplete",
            _item(
                media_id=10,
                sync_state="failed",
                import_status="failed",
                classification_status="pending",
                ai_tagging_status="pending",
                localization_status="waiting_ai_tags",
                failure_reason="source_missing",
            ),
            _media(),
            True,
            False,
            LifecycleKind.APP_MEDIA_FOLLOWUP,
            WorkItemKind.FOLLOWUP,
            False,
            True,
            True,
        ),
        (
            "cloud_hydration_failed_source_attempt_retry",
            _item(sync_state="failed", import_status="failed", failure_reason="cloud_hydration_failed"),
            None,
            None,
            False,
            LifecycleKind.RETRYABLE_SOURCE_FAILURE,
            WorkItemKind.RETRY_SOURCE,
            True,
            True,
            True,
        ),
        (
            "content_changed_after_plan_retry",
            _item(sync_state="failed", import_status="failed", failure_reason="content_changed_after_plan"),
            None,
            None,
            False,
            LifecycleKind.RETRYABLE_SOURCE_FAILURE,
            WorkItemKind.RETRY_SOURCE,
            True,
            True,
            True,
        ),
        (
            "missing_media_row_broken_state",
            _item(
                media_id=10,
                sync_state="imported",
                import_status="imported",
                classification_status="classified",
                ai_tagging_status="ai_tagged",
                localization_status="localized",
            ),
            None,
            None,
            False,
            LifecycleKind.BROKEN_STATE,
            WorkItemKind.BROKEN_STATE,
            False,
            False,
            False,
        ),
        (
            "historical_pending_not_current_priority",
            _item(sync_state="new", import_status="pending"),
            None,
            None,
            False,
            LifecycleKind.HISTORICAL_DIAGNOSTIC,
            WorkItemKind.NOOP_DIAGNOSTIC,
            False,
            False,
            False,
        ),
        (
            "current_import_candidate",
            _item(sync_state="new", import_status="pending"),
            None,
            None,
            True,
            LifecycleKind.IMPORT_CANDIDATE,
            WorkItemKind.IMPORT,
            True,
            True,
            True,
        ),
    ],
)
def test_manual_sync_lifecycle_table_driven_scenarios(
    name: str,
    item: SimpleNamespace,
    media: SimpleNamespace | None,
    app_exists: bool | None,
    current_priority: bool,
    expected_kind: LifecycleKind,
    expected_work: WorkItemKind,
    source_reads: bool,
    consumes_cap: bool,
    can_execute: bool,
) -> None:
    decision = classify_source_item(
        item,
        media=media,
        media_lookup_performed=media is not None or item.media_id is not None,
        app_media_exists=app_exists,
        current_priority=current_priority,
    )

    assert decision.kind == expected_kind, name
    assert decision.work_item_kind == expected_work, name
    assert decision.allowed_source_reads is source_reads, name
    assert decision.consumes_actionable_cap is consumes_cap, name
    assert decision.can_execute is can_execute, name


def test_manual_sync_lifecycle_attempted_and_current_health_are_separate() -> None:
    run_item = SimpleNamespace(item_state="downstream_followup_planned", action="downstream_followup", reason="downstream_followup")
    item = _item(
        media_id=10,
        sync_state="downstream_followup_planned",
        import_status="imported",
        classification_status="classified",
        ai_tagging_status="failed_ai_tagger_model_uncached",
        localization_status="blocked_ai_tagging_failed",
        deferred_reason="downstream_followup",
    )

    decision = classify_source_item(item, media=_media(), media_lookup_performed=True, app_media_exists=True, run_item=run_item)

    assert decision.kind == LifecycleKind.APP_MEDIA_FOLLOWUP
    assert decision.attempted_in_run is True
    assert decision.current_downstream_complete is False
    assert decision.attempted_but_current_incomplete is True
    assert decision.not_processed_continuation is False


def test_manual_sync_lifecycle_operator_status_mapping() -> None:
    assert (
        map_manual_sync_operator_status(
            run_status="completed_with_failures",
            outcome_counts={"failed": 11, "read_timeout": 9, "read_error": 2},
            retryable_source_failure_count=11,
        )
        == "completed_with_retryable_failures"
    )
    assert (
        map_manual_sync_operator_status(
            run_status="completed_with_failures",
            outcome_counts={"failed": 5, "read_timeout": 1},
            retryable_source_failure_count=1,
        )
        == "failed_systemic"
    )
    assert map_manual_sync_operator_status(run_status="completed", outcome_counts={}) == "completed"
    assert map_manual_sync_operator_status(run_status="failed", outcome_counts={"failed": 1}) == "failed_systemic"
    assert (
        map_manual_sync_operator_status(
            run_status="cancelled",
            outcome_counts={"imported": 2},
            unprocessed_import_planned_count=3,
        )
        == "cancelled"
    )
    assert (
        map_manual_sync_operator_status(
            run_status="completed_with_failures",
            outcome_counts={"failed": 11, "read_timeout": 11},
            retryable_source_failure_count=11,
            unprocessed_import_planned_count=75,
            import_stopped_by="stopped_by_failure_budget",
        )
        == "completed_with_retryable_failures_plus_continuation"
    )
    assert (
        map_manual_sync_operator_status(
            run_status="failed",
            outcome_counts={"classification_failed": 2, "ai_tagging_failed": 1},
            downstream_incomplete_count=3,
            import_stopped_by="stopped_by_failure_budget",
        )
        == "failed_systemic"
    )
    assert (
        map_manual_sync_operator_status(
            run_status="failed",
            outcome_counts={"failed": 1},
            unprocessed_import_planned_count=3,
        )
        == "failed_systemic"
    )


def test_manual_sync_operator_label_catalog_covers_pr_r2_operator_terms() -> None:
    catalog = manual_sync_operator_label_catalog()

    assert set(catalog["operator_statuses"]) >= {
        "completed",
        "completed_with_retryable_failures",
        "completed_with_followup_required",
        "completed_with_continuation",
        "completed_with_retryable_failures_plus_continuation",
        "failed_systemic",
        "blocked_preflight",
        "cancelled",
    }
    assert set(catalog["work_item_kinds"]) >= {
        "IMPORT",
        "FOLLOWUP",
        "RETRY_SOURCE",
        "BROKEN_STATE",
        "PLACEHOLDER",
        "NOOP_DIAGNOSTIC",
    }
    assert set(catalog["lifecycle_kinds"]) >= {
        "APP_MEDIA_FOLLOWUP",
        "IMPORT_CANDIDATE",
        "RETRYABLE_SOURCE_FAILURE",
        "PLACEHOLDER_DEFERRED",
        "STABLE_NOOP",
        "HISTORICAL_DIAGNOSTIC",
        "CONTINUATION",
        "BROKEN_STATE",
        "FATAL_BLOCKER",
    }
    for section in catalog.values():
        assert all(value and value != key for key, value in section.items())


def test_manual_sync_lifecycle_plan_state_boundaries() -> None:
    followup = classify_plan_item_state(state="downstream_followup_planned", reason="downstream_followup", media_id=10)
    import_candidate = classify_plan_item_state(state="import_planned")
    placeholder = classify_plan_item_state(state="skipped_placeholder", reason="icloud_placeholder")
    noop = classify_plan_item_state(state="skipped_existing_media", reason="existing_media_hash", media_id=10)
    broken = classify_source_item(
        _item(
            media_id=10,
            sync_state="imported",
            import_status="imported",
            classification_status="pending",
            ai_tagging_status="pending",
            localization_status="waiting_ai_tags",
        ),
        media=_media(),
        media_lookup_performed=True,
        app_media_exists=False,
    )

    assert followup.work_item_kind == WorkItemKind.FOLLOWUP
    assert followup.allowed_source_reads is False
    assert import_candidate.work_item_kind == WorkItemKind.IMPORT
    assert import_candidate.allowed_source_reads is True
    assert placeholder.work_item_kind == WorkItemKind.PLACEHOLDER
    assert placeholder.allowed_source_reads is False
    assert placeholder.can_execute is False
    assert placeholder.consumes_actionable_cap is False
    assert noop.work_item_kind == WorkItemKind.NOOP_DIAGNOSTIC
    assert noop.consumes_actionable_cap is False
    assert broken.work_item_kind == WorkItemKind.BROKEN_STATE
    assert broken.can_execute is False
    assert broken.is_visible_in_normal_ui is True


def test_manual_sync_lifecycle_root_scoped_inventory_excludes_other_roots() -> None:
    root_one_items = [
        _item(id=1, source_root_id=1, sync_state="deferred_unprocessed", import_status="deferred", deferred_reason="not_processed_budget_stop"),
        _item(id=2, source_root_id=1, sync_state="failed", import_status="failed", failure_reason="read_timeout"),
    ]
    other_root = _item(id=3, source_root_id=2, sync_state="failed", import_status="failed", failure_reason="read_timeout")
    all_items = [*root_one_items, other_root]

    scoped_counts = Counter(
        classify_source_item(item).kind.value
        for item in all_items
        if item.source_root_id == 1
    )

    assert scoped_counts == {"CONTINUATION": 1, "RETRYABLE_SOURCE_FAILURE": 1}
