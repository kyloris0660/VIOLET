from __future__ import annotations

from pathlib import Path
import json


ROOT = Path(__file__).resolve().parent.parent
ADMIN_TEMPLATE = ROOT / "frontend" / "templates" / "admin.html"
ADMIN_JS = ROOT / "frontend" / "static" / "js" / "admin.js"
SERVICE_WORKER_JS = ROOT / "frontend" / "static" / "sw.js"
LOCALES = [
    ROOT / "frontend" / "static" / "locales" / "en.json",
    ROOT / "frontend" / "static" / "locales" / "zh-cn.json",
]


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_dynamic_sync_operator_flow_is_primary_and_legacy_controls_are_advanced() -> None:
    template = _text(ADMIN_TEMPLATE)

    operator_index = template.index('id="dynamic-sync-operator-card"')
    start_index = template.index('id="dynamic-sync-start-btn"')
    advanced_index = template.index('id="dynamic-sync-advanced-controls"')
    check_index = template.index('id="dynamic-sync-check-btn"')
    dry_run_index = template.index('id="dynamic-sync-dry-run-btn"')
    hydrated_only_index = template.index('id="dynamic-sync-hydrated-only"')
    hydration_policy_index = template.index('id="dynamic-sync-hydration-policy"')
    threshold_index = template.index('id="dynamic-sync-threshold"')

    assert operator_index < start_index < advanced_index
    assert operator_index < hydration_policy_index < advanced_index
    assert advanced_index < threshold_index
    assert advanced_index < hydrated_only_index < check_index
    assert advanced_index < check_index < dry_run_index
    assert 'id="dynamic-sync-execute-max-files" min="1" max="1000" value="1000"' in template
    assert 'id="dynamic-sync-execute-max-files" min="1" max="5" value="5"' not in template
    assert "开始手动同步" in template
    assert "旧“检查更新”会扫描 source root" in template


def test_dynamic_sync_ui_has_persistent_progress_and_confirmation_actions() -> None:
    template = _text(ADMIN_TEMPLATE)
    script = _text(ADMIN_JS)

    for marker in (
        'id="dynamic-sync-progress"',
        'id="dynamic-sync-progress-label"',
        'id="dynamic-sync-progress-elapsed"',
        'id="dynamic-sync-progress-request"',
        'id="dynamic-sync-progress-meta"',
        'id="dynamic-sync-progress-counts"',
        'id="dynamic-sync-progress-pending"',
        'id="dynamic-sync-progress-events"',
        'id="dynamic-sync-plan-cancel-btn"',
        'id="dynamic-sync-confirm-execute-btn"',
        'id="dynamic-sync-copy-confirmation-btn"',
        'id="dynamic-sync-confirmation-phrase"',
    ):
        assert marker in template or marker in script

    assert "dynamicSyncActionInFlight" in script
    assert "_manualSyncSetProgress" in script
    assert "gui_validation_session_token" in script
    assert "X-Violet-Gui-Client" in script
    assert "useAdvancedHydratedOnly = false" in script
    assert "cloud_aware_non_destructive_read" in script
    assert "POST /api/admin/dynamic-library-sync/manual-sync/plan" in script
    assert "/api/admin/dynamic-library-sync/manual-sync/plan-progress/" in script
    assert "plan_request_id" in script
    assert "cancelManualSyncPlan" in script
    assert "Manual sync plan had no visible progress" in script
    assert "Healthy progress may continue beyond 600s" in script
    assert "_manualSyncStartExecutePendingTicker" in script
    assert "_manualSyncYieldForPaint" in script
    assert "waiting_for_first_backend_progress_heartbeat" in script
    assert "stageStatus: { plan: 'completed', import: 'queued' }" in script
    assert "rowStatus === 'running' ? 'running'" in script
    assert "['running', 'pending'].includes(rowStatus)" not in script
    assert "执行请求已提交" in script
    assert "等待首个后端进度心跳" in script
    assert "pending: true" in script
    assert "const retryReadyForImport = Number(outcomes.retry_source_ready_for_import || 0);" in script
    assert "const nextImportReadyCount = Number(execute.next_import_ready_count || 0) || ((Number(execute.unprocessed_import_planned_count || 0)) + retryReadyForImport);" in script
    assert "待下一次导入=${nextImportReadyCount}" in script
    assert "10 * 60 * 1000" not in script
    assert "/api/admin/dynamic-library-sync/manual-sync/gui-session" in script
    assert "POST /api/admin/dynamic-library-sync/check. This diagnostic path can scan the full root." in script
    assert "Filesystem fallback:" in script
    assert "Fast skip identity:" in script
    assert "Actionable:" in script
    assert "Batch:" in script
    assert "Scan model:" in script
    assert "Root last checked:" in script
    assert "Fast-skipped:" in script
    assert "More batches:" in script
    assert "source item ledger" in script
    assert "_manualSyncWorkItemKindLabel" in script
    assert "重试源文件读取" in script
    assert "状态异常诊断" in script
    assert "无需执行的诊断项" in script
    assert "dynamicSyncRoots = []" in script
    assert "_selectedDynamicSyncRoot" in script
    assert "_renderManualSyncOperatorSummary" in script
    assert "dynamicSyncPlanRoot.addEventListener('change'" in script


def test_dynamic_sync_ui_labels_threshold_and_localization_readiness_for_operators() -> None:
    script = _text(ADMIN_JS)

    assert "Historical diagnostic threshold reached; not a current manual-execute blocker." in script
    assert "Historical diagnostic only; current manual plan decides execute safety." in script
    assert "AI -> localization:" not in script
    assert "Background AI-to-localization chaining" in script
    assert "Manual E2E localization readiness" in script
    assert "expected OFF; manual sync finalizes localization during this run" in script


def test_dynamic_sync_ui_requires_operator_entered_confirmation() -> None:
    template = _text(ADMIN_TEMPLATE)
    script = _text(ADMIN_JS)

    operator_index = template.index('id="dynamic-sync-operator-card"')
    confirmation_index = template.index('id="dynamic-sync-confirmation"')
    advanced_index = template.index('id="dynamic-sync-advanced-controls"')

    assert operator_index < confirmation_index < advanced_index
    assert "useExpectedConfirmation" not in script
    assert "confirmationEl.value = expected" not in script
    assert "_confirmAndExecuteManualSyncPlan" in script
    assert "window.confirm(confirmationText)" in script
    assert "body.confirmation_phrase = operatorConfirmedFullChain ? '' : confirmation;" in script
    assert "body.operator_confirmation_statement = operatorConfirmedFullChain ? operatorStatement : null;" in script
    assert "allowDuringPlanFlow" in script
    assert "production_acceptance_approved = !!this.dynamicSyncProductionMode && (operatorConfirmedFullChain || confirmation === expected)" in script
    assert "complete && actionable && matches && !advancedRetryBlocked" in script
    assert "retrySourceCount" in script
    assert "batchExecutable && actionable" in script
    assert "此计划不可执行，因此不需要操作员确认。" in script
    assert "写入前需要操作员确认：" in script
    assert "高级诊断：精确审计短语" in script
    assert "showAdvancedExecute" in script
    assert "requiresConfirmation = canExecute" in script
    assert "S3A-M1 PRODUCTION MANUAL SYNC EXECUTE" not in script


def test_dynamic_sync_start_flow_counts_retry_source_as_actionable_work() -> None:
    script = _text(ADMIN_JS)

    auto_execute_block = script[script.index("async runManualSyncDryRunPlan") : script.index("async executeManualSyncPlan")]

    assert "const retrySource = (plan.counts || {}).estimated_retry_source_count" in auto_execute_block
    assert "+ retrySource" in auto_execute_block
    assert "advanced_full_rescan_retry_source_execution_not_validated" in auto_execute_block
    assert "const executable = !!((plan.counts || {}).batch_executable || (plan.limits || {}).batch_executable) && !advancedRetryBlocked;" in auto_execute_block
    assert "if (autoExecute && actionable > 0 && executable)" in auto_execute_block


def test_dynamic_sync_work_item_cards_distinguish_normal_and_advanced_retry_source_execution() -> None:
    script = _text(ADMIN_JS)
    render_block = script[script.index("_renderManualSyncPlan(plan)") : script.index("\n    _manualSyncExpectedConfirmationPhrase(plan)")]

    assert "const retrySourceBlockedInAdvancedMode = kind === 'RETRY_SOURCE' && advancedRetryBlocked;" in render_block
    assert "const executable = ['IMPORT', 'FOLLOWUP', 'RETRY_SOURCE'].includes(kind) && !retrySourceBlockedInAdvancedMode;" in render_block
    assert "当前高级模式不可执行" in render_block
    assert "text-green-400" in render_block


def test_dynamic_sync_stage_strip_treats_terminal_non_clean_statuses_as_completed() -> None:
    script = _text(ADMIN_JS)

    for status in (
        "completed_with_failures",
        "completed_with_retryable_failures",
        "completed_with_followup_required",
        "completed_with_continuation",
        "completed_with_retryable_failures_plus_continuation",
        "completed_with_localization_failures",
    ):
        assert status in script

    assert "rowStatus.startsWith('completed_with_')" in script
    assert "completed_with_retryable_failures" in script[script.index("terminalCompletedStageStatuses") :]


def test_dynamic_sync_stage_strip_treats_skipped_run_statuses_as_terminal_not_queued() -> None:
    script = _text(ADMIN_JS)

    assert "rowStatus.startsWith('skipped_') && rowStatus.endsWith('_run')" in script
    assert "skippedTerminalRunStatus ? 'skipped'" in script
    assert "skipped: '已跳过/已停止'" in script


def test_dynamic_sync_ui_does_not_render_raw_i18n_keys_or_internal_blocker_prefixes() -> None:
    script = _text(ADMIN_JS)

    assert "translated !== key" in script
    assert "threshold_warning_historical" not in script
    assert "Blockers:" not in script
    assert "Warnings:" not in script
    assert "manual_execute_blockers" in script
    assert "background_warnings" in script


def test_dynamic_sync_canonical_content_url_is_preserved() -> None:
    script = _text(ADMIN_JS)

    assert "hashIsContentSection" in script
    assert "showHashSection" in script
    assert "window.addEventListener('hashchange'" in script
    assert "urlParams.set('tab', 'content')" in script
    assert "window.history.replaceState({}, '', `${window.location.pathname}?${urlParams.toString()}${window.location.hash}`)" in script
    assert "urlParams.delete('tab')" not in script


def test_dynamic_sync_new_i18n_keys_are_translated() -> None:
    required = {
        "operator_title",
        "start_manual_sync",
        "elapsed",
        "confirm_execute_btn",
        "copy_confirmation_btn",
        "advanced_controls",
        "hydration_policy_cloud_aware",
    }

    for path in LOCALES:
        data = json.loads(path.read_text(encoding="utf-8"))
        sync = data["admin"]["dynamic_library_sync"]
        missing = required - set(sync)
        assert not missing, f"{path.name} missing {sorted(missing)}"
        assert all(not sync[key].startswith("admin.dynamic_library_sync") for key in required)


def test_service_worker_static_cache_tracks_cache_buster() -> None:
    script = _text(SERVICE_WORKER_JS)

    assert "new URL(self.location.href).searchParams.get('v')" in script
    assert "const CACHE_NAME = `violet-${CACHE_VERSION}`;" in script
    assert "new Request(`${asset}?v=${CACHE_VERSION}`, { cache: 'reload' })" in script
    assert "const CACHE_NAME = 'violet-1-41-0';" not in script
