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

    assert operator_index < start_index < advanced_index
    assert operator_index < hydration_policy_index < advanced_index
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
    assert "/api/admin/dynamic-library-sync/manual-sync/gui-session" in script
    assert "POST /api/admin/dynamic-library-sync/check. This diagnostic path can scan the full root." in script


def test_dynamic_sync_ui_requires_operator_entered_confirmation() -> None:
    template = _text(ADMIN_TEMPLATE)
    script = _text(ADMIN_JS)

    operator_index = template.index('id="dynamic-sync-operator-card"')
    confirmation_index = template.index('id="dynamic-sync-confirmation"')
    advanced_index = template.index('id="dynamic-sync-advanced-controls"')

    assert operator_index < confirmation_index < advanced_index
    assert "useExpectedConfirmation" not in script
    assert "confirmationEl.value = expected" not in script
    assert "body.confirmation_phrase = confirmation;" in script
    assert "production_acceptance_approved = !!this.dynamicSyncProductionMode && confirmation === expected" in script
    assert "complete && importable && matches" in script


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
