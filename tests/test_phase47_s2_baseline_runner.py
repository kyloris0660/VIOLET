from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

from scripts import run_phase47_s2_baseline_full_import_ai_localization as s2


def _gate0_blocked(**overrides):
    gate0 = {
        "status": "blocked",
        "blockers": [
            "dynamic_sync_tables_missing",
            "backup_recovery_proof_missing",
            "schema_setup_requires_valid_backup_proof",
        ],
        "warnings": [],
        "backup_recovery": {
            "proof_supplied": False,
            "proof_exists": False,
            "valid": False,
            "validation_error_codes": ["backup_proof_path_missing"],
            "recovery_path_documented": False,
            "path_redacted": True,
            "operator_instructions": s2.backup_operator_instructions("blombooru"),
        },
        "schema": {
            "before": {"tables_present": [], "tables_missing": list(s2.DYNAMIC_SYNC_TABLES), "indexes_present": {}},
            "after": {"tables_present": [], "tables_missing": list(s2.DYNAMIC_SYNC_TABLES), "indexes_present": {}},
            "ensure": {
                "status": "blocked_backup_required",
                "ran": False,
                "approved": False,
                "tables_missing_before": list(s2.DYNAMIC_SYNC_TABLES),
                "tables_missing_after": list(s2.DYNAMIC_SYNC_TABLES),
                "additive_only": True,
                "destructive_operations": [],
            },
        },
        "input_root_registration": {"requested": False, "registered_count": 0, "validated_count": 0, "failed_count": 0, "roots": []},
        "safety": {
            "additive_schema_only": True,
            "drop_truncate_delete_reset": False,
            "production_media_source_mutation": False,
            "import_classification_ai_localization_executed": False,
        },
    }
    gate0.update(overrides)
    return gate0


def _blocked_readiness(**overrides):
    gate0 = overrides.get("gate0") or _gate0_blocked()
    readiness = {
        "passed": False,
        "blockers": sorted(set(["dynamic_sync_tables_missing", *(gate0.get("blockers", []) or [])])),
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
        "backup_recovery": {"proof_exists": False, "valid": False, "path_redacted": True},
        "ai_model": {"checked": True, "available": True, "model_downloaded": True},
        "llm_localization": {
            "operator_approved": True,
            "enabled": True,
            "provider_configured": True,
            "model_configured": True,
            "base_url_configured": True,
            "api_key_configured": True,
            "auto_enabled": True,
            "background_enabled": False,
            "secrets_recorded": False,
        },
        "proper_noun_safeguards": {
            "search_alias_trust_policy": "manual_static_or_operator_reviewed_only",
            "entity_truth_created": False,
        },
        "automatic_production_sync": {"enabled": False, "remains_opt_in": True},
        "gate0": gate0,
    }
    readiness.update(overrides)
    return readiness


def _passed_readiness(**overrides):
    gate0 = _gate0_blocked(
        status="passed",
        blockers=[],
        backup_recovery={
            "proof_supplied": True,
            "proof_exists": True,
            "valid": True,
            "recovery_path_documented": True,
            "path_redacted": True,
        },
        schema={
            "before": {"tables_present": list(s2.DYNAMIC_SYNC_TABLES), "tables_missing": [], "indexes_present": {}},
            "after": {"tables_present": list(s2.DYNAMIC_SYNC_TABLES), "tables_missing": [], "indexes_present": {}},
            "ensure": {"status": "not_needed", "ran": False, "approved": False, "tables_missing_before": [], "tables_missing_after": [], "additive_only": True, "destructive_operations": []},
        },
        input_root_registration={"requested": False, "registered_count": 0, "validated_count": 0, "failed_count": 0, "roots": []},
    )
    readiness = _blocked_readiness(
        passed=True,
        blockers=[],
        dynamic_schema={"tables_present": list(s2.DYNAMIC_SYNC_TABLES), "tables_missing": []},
        source_roots={"active_count": 1, "registered_count": 1, "valid_count": 1},
        backup_recovery=gate0["backup_recovery"],
        gate0=gate0,
    )
    readiness.update(overrides)
    return readiness


def test_gate1_block_writes_private_ledgers_and_public_report(tmp_path, monkeypatch):
    monkeypatch.setattr(s2, "PUBLIC_REPORT_MD", tmp_path / "public.md")
    monkeypatch.setattr(s2, "PUBLIC_REPORT_JSON", tmp_path / "public.json")
    monkeypatch.setattr(s2, "output_dir_allowed", lambda _path: True)
    monkeypatch.setattr(s2, "run_gate0_preparation", lambda _args: _gate0_blocked())
    monkeypatch.setattr(s2, "collect_readiness", lambda _args, gate0=None: _blocked_readiness(gate0=gate0))

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

    assert summary["status"] == "blocked_schema_backup_required"
    assert summary["pipeline_contract"]["claims"]["target_met"] is False
    assert summary["import_results"]["executed"] is False
    assert summary["gate0"]["schema"]["ensure"]["ran"] is False
    assert summary["gate0"]["backup_recovery"]["operator_instructions"]
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
    monkeypatch.setattr(s2, "run_gate0_preparation", lambda _args: _gate0_blocked())
    monkeypatch.setattr(s2, "collect_readiness", lambda args, gate0=None: fake_readiness(args))

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
    assert summary["status"] == "blocked_schema_backup_required"
    assert summary["classification_results"]["executed"] is False
    assert summary["ai_tagging_results"]["executed"] is False
    assert summary["localization_results"]["llm_called"] is False


def test_missing_dynamic_tables_without_backup_is_actionable_and_no_migration(tmp_path, monkeypatch):
    called = {"ensure": 0}

    def forbid_ensure(*_args, **_kwargs):
        called["ensure"] += 1
        raise AssertionError("schema ensure must not run without backup proof")

    monkeypatch.setattr(s2, "PUBLIC_REPORT_MD", tmp_path / "public.md")
    monkeypatch.setattr(s2, "PUBLIC_REPORT_JSON", tmp_path / "public.json")
    monkeypatch.setattr(s2, "output_dir_allowed", lambda _path: True)
    monkeypatch.setattr(s2, "ensure_dynamic_sync_schema", forbid_ensure)
    monkeypatch.setattr(s2, "run_gate0_preparation", lambda _args: _gate0_blocked())
    monkeypatch.setattr(s2, "collect_readiness", lambda _args, gate0=None: _blocked_readiness(gate0=gate0))

    args = s2.build_parser().parse_args(["--readiness", "--output-dir", str(tmp_path / "private"), "--write-public-report"])
    args.run_id = "test-run"

    summary = s2.run_pipeline(args)

    assert called["ensure"] == 0
    assert summary["status"] == "blocked_schema_backup_required"
    assert "schema_setup_requires_valid_backup_proof" in summary["readiness"]["blockers"]
    assert summary["gate0"]["backup_recovery"]["operator_instructions"]


def test_backup_proof_path_alone_is_invalid_without_required_contents(tmp_path):
    proof_path = tmp_path / "backup-proof.json"
    proof_path.write_text(json.dumps({"database": "blombooru"}), encoding="utf-8")
    args = s2.build_parser().parse_args(["--readiness", "--backup-proof-path", str(proof_path)])

    status = s2.backup_proof_status(args, actual_db_name="blombooru")

    assert status["proof_exists"] is True
    assert status["valid"] is False
    assert "backup_command_exit_code_not_zero" in status["validation_error_codes"]
    assert "backup_dump_path_missing" in status["validation_error_codes"]
    assert "backup_proof_created_at_missing" in status["validation_error_codes"]
    assert status["operator_instructions"]
    assert status["path_redacted"] is True


def test_valid_backup_proof_requires_matching_db_dump_and_recovery_notes(tmp_path):
    dump_path = tmp_path / "prod.dump"
    dump_path.write_bytes(b"pgdump")
    proof_path = tmp_path / "backup-proof.json"
    proof_path.write_text(
        json.dumps(
            {
                "database": "blombooru",
                "pg_dump_exit_code": 0,
                "dump_file": str(dump_path),
                "created_at": "2026-06-18T00:00:00Z",
                "recovery_notes": "restore with pg_restore into verified production DB",
            }
        ),
        encoding="utf-8",
    )
    args = s2.build_parser().parse_args(["--readiness", "--backup-proof-path", str(proof_path)])

    status = s2.backup_proof_status(args, actual_db_name="blombooru")

    assert status["valid"] is True
    assert status["expected_database_matches"] is True
    assert status["actual_database_matches"] is True
    assert status["backup_command_exit_code_zero"] is True
    assert status["dump_file_exists"] is True
    assert status["dump_file_non_empty"] is True
    assert status["recovery_path_documented"] is True


def test_invalid_backup_proof_blocks_schema_ensure_even_with_schema_approval(tmp_path, monkeypatch):
    class FakeScalar:
        def scalar(self):
            return "blombooru"

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args, **_kwargs):
            return FakeScalar()

    class FakeEngine:
        def connect(self):
            return FakeConnection()

        def dispose(self):
            pass

    called = {"ensure": 0}

    def forbid_ensure(*_args, **_kwargs):
        called["ensure"] += 1
        raise AssertionError("schema ensure must not run with invalid backup proof")

    proof_path = tmp_path / "bad-proof.json"
    proof_path.write_text(json.dumps({"database": "blombooru"}), encoding="utf-8")
    monkeypatch.setenv("VIOLET_ENV", "production")
    monkeypatch.setenv("VIOLET_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setattr(s2, "create_engine", lambda *_args, **_kwargs: FakeEngine())
    monkeypatch.setattr(
        s2,
        "schema_snapshot",
        lambda _conn: {
            "tables_present": [],
            "tables_missing": list(s2.DYNAMIC_SYNC_TABLES),
            "indexes_present": {},
        },
    )
    monkeypatch.setattr(s2, "ensure_dynamic_sync_schema", forbid_ensure)
    args = s2.build_parser().parse_args(
        ["--readiness", "--approve-schema-setup", "--backup-proof-path", str(proof_path)]
    )
    args.run_id = "test-run"

    gate0 = s2.run_gate0_preparation(args)

    assert called["ensure"] == 0
    assert "backup_recovery_proof_invalid" in gate0["blockers"]
    assert "schema_setup_requires_valid_backup_proof" in gate0["blockers"]
    assert gate0["schema"]["ensure"]["ran"] is False


def test_schema_ensure_uses_existing_migration_and_preserves_rows():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE blombooru_media (id INTEGER PRIMARY KEY)"))
        conn.execute(text("INSERT INTO blombooru_media (id) VALUES (1)"))
        before = s2.schema_snapshot(conn)

    result = s2.ensure_dynamic_sync_schema(engine, before)

    with engine.connect() as conn:
        after = s2.schema_snapshot(conn)
        media_count = conn.execute(text("SELECT COUNT(*) FROM blombooru_media")).scalar()

    assert result["ran"] is True
    assert result["path_used"] == "migrate_add_dynamic_library_sync_tables"
    assert result["destructive_operations"] == []
    assert result["tables_missing_before"] == list(s2.DYNAMIC_SYNC_TABLES)
    assert result["tables_missing_after"] == []
    assert after["tables_missing"] == []
    assert media_count == 1


def test_source_root_missing_reports_clear_blocked_registration(tmp_path):
    args = s2.build_parser().parse_args(
        [
            "--readiness",
            "--source-root",
            str(tmp_path / "missing"),
            "--source-label",
            "missing-root",
            "--output-dir",
            str(tmp_path / "private"),
        ]
    )
    args.run_id = "test-run"
    result = s2.register_phase_source_roots(args)

    assert result["requested"] is True
    assert result["registered_count"] == 0
    assert result["failed_count"] == 1
    assert result["roots"][0]["path_redacted"] is True
    assert "private_path" not in result["roots"][0]


def test_source_root_validation_can_use_local_library_paths_config(tmp_path, monkeypatch):
    source_root = tmp_path / "configured-source"
    source_root.mkdir()
    monkeypatch.setenv("LOCAL_LIBRARY_PATHS", str(source_root))
    args = s2.build_parser().parse_args(["--readiness"])
    args.run_id = "test-run"

    result = s2.register_phase_source_roots(args)

    assert result["requested"] is True
    assert result["input_source"] == "LOCAL_LIBRARY_PATHS"
    assert result["registration_requested"] is False
    assert result["validated_count"] == 1
    assert result["registered_count"] == 0
    assert result["roots"][0]["run_local_label"] == "root-1"


def test_private_output_dir_is_restricted_to_phase_local_manifests(tmp_path):
    assert s2.output_dir_allowed(s2.DEFAULT_OUTPUT_DIR)
    assert s2.output_dir_allowed(s2.DEFAULT_OUTPUT_DIR / "run-1")
    assert not s2.output_dir_allowed(tmp_path / "private")
    assert not s2.output_dir_allowed(s2.DEFAULT_OUTPUT_DIR / "backup")


def test_output_dir_cannot_overlap_source_root(tmp_path, monkeypatch):
    monkeypatch.setattr(s2, "PUBLIC_REPORT_MD", tmp_path / "public.md")
    monkeypatch.setattr(s2, "PUBLIC_REPORT_JSON", tmp_path / "public.json")
    source_root = tmp_path / "source"
    output_dir = source_root / ".local_manifests" / s2.PHASE_SLUG
    output_dir.mkdir(parents=True)
    monkeypatch.setattr(s2, "output_dir_allowed", lambda _path: True)

    args = s2.build_parser().parse_args(
        ["--readiness", "--source-root", str(source_root), "--output-dir", str(output_dir)]
    )
    args.run_id = "test-run"
    summary = s2.build_summary(args, _blocked_readiness())

    with pytest.raises(s2.S2BlockedError, match="unsafe_output_dir_overlaps_source_root"):
        s2.write_outputs(args, summary)


def test_llm_approval_still_requires_real_config(monkeypatch):
    for key in (
        "TAG_TRANSLATION_LLM_ENABLED",
        "TAG_TRANSLATION_LLM_PROVIDER",
        "TAG_TRANSLATION_LLM_MODEL",
        "TAG_TRANSLATION_LLM_BASE_URL",
        "TAG_TRANSLATION_LLM_API_KEY",
        "TAG_TRANSLATION_AUTO_ENABLED",
        "TAG_TRANSLATION_BACKGROUND_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)
    args = s2.build_parser().parse_args(["--readiness", "--approve-llm-localization"])

    readiness, blockers = s2.llm_localization_readiness(args)

    assert readiness["operator_approved"] is True
    assert "TAG_TRANSLATION_LLM_ENABLED_false" in blockers
    assert "TAG_TRANSLATION_LLM_PROVIDER_missing" in blockers
    assert "TAG_TRANSLATION_LLM_MODEL_missing" in blockers
    assert "TAG_TRANSLATION_LLM_BASE_URL_missing" in blockers
    assert "TAG_TRANSLATION_LLM_API_KEY_missing" in blockers
    assert "tag_translation_execution_path_not_configured" in blockers


def test_source_root_registration_is_blocked_until_env_db_storage_schema_identity_is_clean(tmp_path, monkeypatch):
    class FakeScalar:
        def scalar(self):
            return "blombooru"

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args, **_kwargs):
            return FakeScalar()

    class FakeEngine:
        def connect(self):
            return FakeConnection()

        def dispose(self):
            pass

    calls = {"register": 0}
    source_root = tmp_path / "source"
    source_root.mkdir()
    monkeypatch.setenv("VIOLET_ENV", "development")
    monkeypatch.setenv("VIOLET_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setattr(s2, "create_engine", lambda *_args, **_kwargs: FakeEngine())
    monkeypatch.setattr(
        s2,
        "schema_snapshot",
        lambda _conn: {
            "tables_present": list(s2.DYNAMIC_SYNC_TABLES),
            "tables_missing": [],
            "indexes_present": {},
        },
    )
    monkeypatch.setattr(
        s2,
        "create_backup_proof",
        lambda _args, actual_db_name=None: {"proof_exists": True, "valid": True, "path_redacted": True},
    )

    def forbidden_register(_args):
        calls["register"] += 1
        raise AssertionError("source-root registration must wait for clean identity gates")

    monkeypatch.setattr(s2, "register_phase_source_roots", forbidden_register)
    args = s2.build_parser().parse_args(
        ["--readiness", "--source-root", str(source_root), "--register-source-root"]
    )
    args.run_id = "test-run"

    gate0 = s2.run_gate0_preparation(args)

    assert calls["register"] == 0
    assert "VIOLET_ENV_not_production" in gate0["blockers"]
    assert "input_root_registration_skipped_until_identity_storage_schema_ready" in gate0["warnings"]


def test_readiness_passed_runs_fresh_dryrun_and_stops_without_execute(tmp_path, monkeypatch):
    calls = {"dry_run": 0}

    def fake_dry_run(_args, _readiness):
        calls["dry_run"] += 1
        return {
            "stage": "dynamic_sync_dry_run",
            "status": "completed",
            "executed": True,
            "target_met": False,
            "dry_run": True,
            "total_seen": 2,
            "pending_new": 2,
            "pending_changed": 0,
            "pending_deferred": 0,
            "unsupported": 0,
            "failed": 0,
            "missing": 0,
            "cloud_only_or_icloud_unavailable": 0,
            "estimated_import_batches": 1,
            "estimated_ai_tagging_workload": 2,
            "item_failures_recorded": True,
        }

    monkeypatch.setattr(s2, "PUBLIC_REPORT_MD", tmp_path / "public.md")
    monkeypatch.setattr(s2, "PUBLIC_REPORT_JSON", tmp_path / "public.json")
    monkeypatch.setattr(s2, "output_dir_allowed", lambda _path: True)
    monkeypatch.setattr(s2, "run_gate0_preparation", lambda _args: _passed_readiness()["gate0"])
    monkeypatch.setattr(s2, "collect_readiness", lambda _args, gate0=None: _passed_readiness(gate0=gate0))
    monkeypatch.setattr(s2, "run_fresh_dynamic_sync_dry_run", fake_dry_run)

    args = s2.build_parser().parse_args(["--readiness", "--output-dir", str(tmp_path / "private"), "--write-public-report"])
    args.run_id = "test-run"
    summary = s2.run_pipeline(args)

    assert calls["dry_run"] == 1
    assert summary["status"] == "dry_run_complete_execute_not_requested"
    assert summary["dynamic_sync_dry_run"]["executed"] is True
    assert summary["pipeline_contract"]["claims"]["target_met"] is False
    assert summary["import_results"]["executed"] is False


def test_execute_after_dry_run_blocks_when_import_execution_not_implemented(tmp_path, monkeypatch):
    def fake_dry_run(_args, _readiness):
        return {
            "stage": "dynamic_sync_dry_run",
            "status": "completed",
            "executed": True,
            "target_met": False,
            "dry_run": True,
            "total_seen": 1,
            "pending_new": 1,
            "pending_changed": 0,
            "pending_deferred": 0,
            "unsupported": 0,
            "failed": 0,
            "missing": 0,
            "cloud_only_or_icloud_unavailable": 0,
            "estimated_import_batches": 1,
            "estimated_ai_tagging_workload": 1,
            "item_failures_recorded": True,
        }

    monkeypatch.setattr(s2, "PUBLIC_REPORT_MD", tmp_path / "public.md")
    monkeypatch.setattr(s2, "PUBLIC_REPORT_JSON", tmp_path / "public.json")
    monkeypatch.setattr(s2, "output_dir_allowed", lambda _path: True)
    monkeypatch.setattr(s2, "run_gate0_preparation", lambda _args: _passed_readiness()["gate0"])
    monkeypatch.setattr(s2, "collect_readiness", lambda _args, gate0=None: _passed_readiness(gate0=gate0))
    monkeypatch.setattr(s2, "run_fresh_dynamic_sync_dry_run", fake_dry_run)

    args = s2.build_parser().parse_args(
        [
            "--execute",
            "--confirm-execution",
            s2.CONFIRM_PHRASE,
            "--output-dir",
            str(tmp_path / "private"),
            "--write-public-report",
        ]
    )
    args.run_id = "test-run"
    summary = s2.run_pipeline(args)

    assert summary["status"] == "blocked_execute_import_not_implemented"
    assert summary["safety"]["stopped_by_rule"]
    assert summary["import_results"]["executed"] is False


def test_dynamic_sync_dry_run_summary_uses_run_local_root_labels_only():
    args = s2.build_parser().parse_args(["--readiness"])
    raw = {
        "status": "completed",
        "dry_run": True,
        "summary": {
            "root_summaries": [
                {
                    "root_id": 7,
                    "label": "private-family-photos",
                    "path_hash": "abcdef0123456789",
                    "counts": {"seen": 1},
                }
            ]
        },
    }

    summary = s2.summarize_dynamic_sync_dry_run(raw, args, _passed_readiness())
    encoded = json.dumps(summary, sort_keys=True)

    assert "path_hash" not in encoded
    assert "abcdef" not in encoded
    assert "private-family-photos" not in encoded
    assert summary["root_summaries"][0]["run_local_root_label"] == "root-1"


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
