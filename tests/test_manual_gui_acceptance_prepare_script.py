from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_manual_gui_acceptance_prepare_script_is_preflight_only():
    script = ROOT / "scripts" / "prepare_s3a_m2_manual_gui_acceptance.ps1"
    wrapper = ROOT / "scripts" / "prepare_s3a_m2_manual_gui_acceptance.cmd"

    assert script.exists()
    assert wrapper.exists()

    text = script.read_text(encoding="utf-8")
    lower = text.casefold()

    assert "manual_acceptance_preflight" in text
    assert "profile-status" in text
    assert "diagnostic-summary" in text
    assert "audit_active_violet_servers.py" in text
    assert "open-manual-sync" in text
    assert "validate_s3a_m2_gui_execute_acceptance.py" in text

    forbidden_tokens = [
        "/api/admin/dynamic-library-sync/manual-sync/execute",
        "create_manual_sync_execute_run",
        "execute_manual_sync_run",
        "profile-repair",
        "priority_backlog_repair",
        "run_s3a_m2_production_delta_execute",
    ]
    for token in forbidden_tokens:
        assert token.casefold() not in lower


def test_manual_gui_acceptance_cmd_wraps_powershell_script():
    wrapper = ROOT / "scripts" / "prepare_s3a_m2_manual_gui_acceptance.cmd"
    text = wrapper.read_text(encoding="utf-8").casefold()

    assert "powershell.exe" in text
    assert "prepare_s3a_m2_manual_gui_acceptance.ps1" in text
