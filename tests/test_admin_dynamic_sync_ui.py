from __future__ import annotations

from pathlib import Path
import json


ROOT = Path(__file__).resolve().parent.parent
ADMIN_TEMPLATE = ROOT / "frontend" / "templates" / "admin.html"
ADMIN_JS = ROOT / "frontend" / "static" / "js" / "admin.js"
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

    assert operator_index < start_index < advanced_index
    assert advanced_index < check_index < dry_run_index
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
    assert "POST /api/admin/dynamic-library-sync/manual-sync/plan" in script
    assert "POST /api/admin/dynamic-library-sync/check. This diagnostic path can scan the full root." in script


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
    }

    for path in LOCALES:
        data = json.loads(path.read_text(encoding="utf-8"))
        sync = data["admin"]["dynamic_library_sync"]
        missing = required - set(sync)
        assert not missing, f"{path.name} missing {sorted(missing)}"
        assert all(not sync[key].startswith("admin.dynamic_library_sync") for key in required)
