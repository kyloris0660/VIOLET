from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_phase47_s2_baseline_full_import_ai_localization as s2


def _blocked_readiness(**overrides):
    readiness = {
        "passed": False,
        "blockers": ["dynamic_sync_tables_missing"],
        "warnings": [],
        "python_env": {
            "expected_python_checked": True,
            "check_python_env_passed": True,
            "public_executable_name": "python.exe",
            "executable_path_redacted": True,
        },
        "git": {
            "branch": s2.BRANCH,
            "head_sha": "abc123",
            "origin_main_sha": "abc123",
            "based_on_origin_main": True,
        },
        "db_identity": {
            "host": "localhost",
            "port": 5432,
            "database": "blombooru",
            "connected_database": "blombooru",
            "username_present": True,
            "password_present": True,
            "password_value_recorded": False,
            "db_resolution": {
                "password_value_recorded": False,
                "runner_matches_app_equivalent": True,
                "urls_match": True,
            },
        },
        "app_settings_db_identity_matches_execution_db": True,
        "production_storage": {"explicitly_set": True, "paths_redacted": True},
        "dynamic_schema": {
            "tables_present": [],
            "tables_missing": list(s2.DYNAMIC_SYNC_TABLES),
        },
        "source_roots": {"active_count": 0, "registered_count": 0, "valid_count": 0},
        "backup_recovery": {"proof_exists": False, "path_redacted": True},
        "ai_model": {"checked": True, "available": True, "model_downloaded": True},
        "llm_localization": {
            "operator_approved": True,
            "enabled": True,
            "api_key_configured": True,
            "secrets_recorded": False,
        },
        "proper_noun_safeguards": {
            "search_alias_trust_policy": "manual_static_or_operator_reviewed_only",
            "entity_truth_created": False,
        },
        "automatic_production_sync": {"enabled": False, "remains_opt_in": True},
    }
    readiness.update(overrides)
    return readiness


def test_gate1_block_writes_private_ledgers_and_public_report(tmp_path, monkeypatch):
    monkeypatch.setattr(s2, "PUBLIC_REPORT_MD", tmp_path / "public.md")
    monkeypatch.setattr(s2, "PUBLIC_REPORT_JSON", tmp_path / "public.json")
    monkeypatch.setattr(s2, "output_dir_allowed", lambda _path: True)
    monkeypatch.setattr(s2, "collect_readiness", lambda _args: _blocked_readiness())

    args = s2.build_parser().parse_args(
        [
            "--readiness",
            "--output-dir",
            str(tmp_path / "private"),
            "--write-public-report",
            "--approve-llm-localization",
        ]
    )
    args.run_id = "test-run"

    summary = s2.run_pipeline(args)

    assert summary["status"] == "blocked_gate1"
    assert summary["pipeline_contract"]["claims"]["target_met"] is False
    assert summary["import_results"]["executed"] is False
    for name in s2.PRIVATE_LEDGER_NAMES:
        assert (tmp_path / "private" / name).exists()
    assert (tmp_path / "public.md").exists()
    assert json.loads((tmp_path / "public.json").read_text(encoding="utf-8"))["public_redaction"]["passed"] is True


def test_execute_requires_exact_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(s2, "output_dir_allowed", lambda _path: True)
    args = s2.build_parser().parse_args(
        [
            "--execute",
            "--confirm-execution",
            "wrong",
            "--output-dir",
            str(tmp_path / "private"),
        ]
    )
    args.run_id = "test-run"

    with pytest.raises(s2.S2BlockedError, match="execute_confirmation_missing_or_wrong"):
        s2.run_pipeline(args)


def test_gate1_block_prevents_execute_path(tmp_path, monkeypatch):
    calls = {"readiness": 0}

    def fake_readiness(_args):
        calls["readiness"] += 1
        return _blocked_readiness()

    monkeypatch.setattr(s2, "PUBLIC_REPORT_MD", tmp_path / "public.md")
    monkeypatch.setattr(s2, "PUBLIC_REPORT_JSON", tmp_path / "public.json")
    monkeypatch.setattr(s2, "output_dir_allowed", lambda _path: True)
    monkeypatch.setattr(s2, "collect_readiness", fake_readiness)

    args = s2.build_parser().parse_args(
        [
            "--execute",
            "--confirm-execution",
            s2.CONFIRM_PHRASE,
            "--output-dir",
            str(tmp_path / "private"),
        ]
    )
    args.run_id = "test-run"

    summary = s2.run_pipeline(args)

    assert calls["readiness"] == 1
    assert summary["status"] == "blocked_gate1"
    assert summary["classification_results"]["executed"] is False
    assert summary["ai_tagging_results"]["executed"] is False
    assert summary["localization_results"]["llm_called"] is False


def test_public_redaction_rejects_paths_and_tokens():
    redaction = s2.scan_public_output(
        {
            "path": r"C:\\Users\\private\\Pictures\\secret.jpg",
            "token": "Bearer abcdefghijklmnop",
        }
    )

    assert redaction["passed"] is False
    assert redaction["finding_count"] >= 1
    assert redaction["findings_redacted"] is True


def test_proper_noun_alias_trust_policy_excludes_unreviewed_llm():
    from app.utils.search_parser import _translation_alias_trusted_for_search

    assert _translation_alias_trusted_for_search(
        SimpleNamespace(category="character", source="llm", status="translated", needs_review=False)
    ) is False
    assert _translation_alias_trusted_for_search(
        SimpleNamespace(category="character", source="llm", status="reviewed", needs_review=False)
    ) is True
    assert _translation_alias_trusted_for_search(
        SimpleNamespace(category="character", source="manual", status="reviewed", needs_review=False)
    ) is True
    assert _translation_alias_trusted_for_search(
        SimpleNamespace(category="general", source="llm", status="translated", needs_review=True)
    ) is True
