from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import Base
from app.enums import FileTypeEnum
from app.models import DynamicSourceItem, DynamicSourceRoot, DynamicSyncRun, Media
import scripts.run_s3a_m2_delta_e2e_with_telemetry as s3a_m2_runner
from scripts.run_s3a_m2_delta_e2e_with_telemetry import (
    _placeholder_rows_from_plan,
    build_standard_pipeline_flow,
    build_ledger_pending_plan,
    build_source_delta_plan,
    refresh_completion_claims,
    ResourceTelemetryMonitor,
    S3AM2Blocked,
    StageTracker,
    s3a_m2_approval_phrase,
    summarize_telemetry,
)
from scripts.diagnose_s3a_m2_ai_tag_assignments import load_run_ids_from_summary


def test_s3a_m2_approval_phrase_is_plan_hash_bound() -> None:
    phrase = s3a_m2_approval_phrase("abcdef1234567890")

    assert phrase == "I APPROVE S3A-M2 PRODUCTION DELTA E2E abcdef123456"


def test_s3a_m2_incident_diagnostic_requires_explicit_or_reported_run_ids(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="s3a_m2_summary_missing_run_ids"):
        load_run_ids_from_summary(tmp_path / "missing-summary.json")

    empty_summary = tmp_path / "summary.json"
    empty_summary.write_text(json.dumps({"execute": {}}), encoding="utf-8")

    with pytest.raises(RuntimeError, match="s3a_m2_summary_missing_run_ids"):
        load_run_ids_from_summary(empty_summary)


def test_s3a_m2_runner_rejects_cap_above_approved_ceiling_before_configure(monkeypatch) -> None:
    def fail_configure(_args):
        raise AssertionError("configure_phase_env must not run for over-ceiling cap")

    monkeypatch.setattr(s3a_m2_runner, "configure_phase_env", fail_configure)

    assert s3a_m2_runner.main(["--dry-run", "--delta-cap", "1001"]) == 2


def test_s3a_m2_runner_rejects_non_positive_translation_batch_max_before_configure(monkeypatch) -> None:
    def fail_configure(_args):
        raise AssertionError("configure_phase_env must not run for invalid translation batch max")

    monkeypatch.setattr(s3a_m2_runner, "configure_phase_env", fail_configure)

    assert s3a_m2_runner.main(["--dry-run", "--translation-batch-max-items", "0"]) == 2


def test_s3a_m2_telemetry_summary_uses_public_safe_artifact_label(tmp_path: Path) -> None:
    samples_path = tmp_path / "resource-samples.jsonl"
    samples = [
        {
            "timestamp": "2026-06-26T00:00:00Z",
            "stage": "ai_tagging",
            "provider": {"actual_provider": "DmlExecutionProvider"},
            "psutil_available": True,
            "nvidia_smi_available": True,
            "system": {"ram_percent": 41.5},
            "process": {"rss_bytes": 123456},
            "gpu": [
                {
                    "name": "Test GPU",
                    "utilization_gpu_percent": 72.0,
                    "memory_used_mib": 2048.0,
                }
            ],
        }
    ]
    samples_path.write_text("\n".join(json.dumps(row) for row in samples) + "\n", encoding="utf-8")

    summary = summarize_telemetry(samples_path)

    assert summary["status"] == "collected"
    assert summary["actual_provider"] == "DmlExecutionProvider"
    assert summary["gpu_provider_used"] is True
    assert summary["max_gpu_memory_used_mib"] == 2048.0
    assert summary["peak_gpu_utilization_percent"] == 72.0
    assert summary["raw_samples_path_redacted"] is True
    assert str(tmp_path) not in json.dumps(summary, sort_keys=True)


def test_s3a_m2_telemetry_summary_reads_nested_provider_provenance(tmp_path: Path) -> None:
    samples_path = tmp_path / "resource-samples.jsonl"
    sample = {
        "timestamp": "2026-06-26T00:00:00Z",
        "stage": "localization",
        "provider": {
            "backend": "onnxruntime",
            "provider": {
                "actual_provider": "DmlExecutionProvider",
                "actual_onnx_provider_loaded": "DmlExecutionProvider",
            },
        },
        "nvidia_smi_available": True,
        "gpu": [{"name": "Test GPU", "utilization_gpu_percent": 55, "memory_used_mib": 1024}],
    }
    samples_path.write_text(json.dumps(sample) + "\n", encoding="utf-8")

    summary = summarize_telemetry(samples_path)

    assert summary["actual_provider"] == "DmlExecutionProvider"
    assert summary["gpu_provider_used"] is True
    assert summary["cpu_fallback_observed"] is False


def test_s3a_m2_telemetry_monitor_rejects_output_outside_approved_tree(tmp_path: Path) -> None:
    with pytest.raises(S3AM2Blocked, match="telemetry_dir_outside_approved_tree"):
        ResourceTelemetryMonitor(
            telemetry_dir=tmp_path,
            stage_tracker=StageTracker(),
            provider_getter=lambda: {},
        )


def test_s3a_m2_completion_claim_requires_launcher_validation() -> None:
    summary = {
        "status": "completed_with_followup_required",
        "pipeline_contract": {
            "contract_id": "s3a_m2_production_delta_e2e_contract_v1",
            "fresh_dry_run_completed": True,
            "claims": {"target_met": False},
        },
        "production_acceptance": {"performed": True},
        "controlled_delta": {"cap_exceeded": False},
        "dry_run": {"total_seen": 3, "partial_scan": False, "state_counts": {"skipped_placeholder": 0}},
        "readiness": {"passed": True},
        "ledger_consistency": {"passed": True},
        "public_redaction": {"passed": True, "finding_count": 0},
        "public_reports": {
            "markdown_report_path": "docs/reports/s3a-m2-production-delta-e2e.md",
            "summary_json_path": "docs/reports/s3a-m2-production-delta-e2e-summary.json",
        },
        "safety": {"private_paths_or_hashes_in_public_report": False},
        "localization": {"status": "completed", "failed": 0},
        "localization_diagnosis": {
            "diagnosis": "benign_all_localizable_tags_already_localized_or_newly_localized",
            "tags_requiring_localization_after_runner": 0,
        },
        "placeholder_hydration": {
            "status": "completed",
            "remaining_placeholders_after_hydration": 0,
            "source_content_written": False,
            "source_deleted_moved_renamed": False,
        },
        "final_inventory": {
            "current_importable_hydrated_supported_items": 0,
            "placeholders_remaining": 0,
        },
        "execute": {
            "status": "completed",
            "imported": 3,
            "classified": 3,
            "ai_tagged": 3,
            "provider_provenance": {"provider": {"actual_provider": "DmlExecutionProvider"}},
        },
        "classification": {"reported": True, "count": 3, "failed": 0},
        "ai_tagging": {
            "reported": True,
            "count": 3,
            "failed": 0,
            "mature_media_tag_policy": True,
            "proper_nouns_suggestion_only": False,
            "no_sourceconcept_or_entity_truth_from_ai_only_tags": True,
        },
        "gpu_telemetry": {"status": "collected"},
        "launcher_web_admin_acceptance": {"validated": False, "status": "pending"},
        "ai_tag_assignment_incident": {
            "status": "repaired",
            "public_safe": True,
            "after": {
                "all_ai_assignments_are_suggestions": False,
                "high_conf_nonproper_expected_normal_count": 2,
                "high_conf_nonproper_incorrect_suggestion_count": 0,
                "high_conf_nonproper_normal_count": 2,
                "high_conf_proper_expected_normal_count": 1,
                "high_conf_proper_incorrect_suggestion_count": 0,
                "high_conf_proper_normal_count": 1,
                "proper_noun_non_suggestion_count": 1,
            },
            "entity_truth_violations_found": 0,
            "localization_remaining_gap": 0,
            "ui_verification": {
                "status": "passed",
                "public_safe": True,
                "sample_count": 2,
                "normal_visible_pass_count": 2,
                "proper_suggestion_visible_pass_count": 1,
            },
        },
        "cohort_self_audit": {
            "status": "passed_after_repair",
            "public_safe": True,
            "normal_ai_tag_semantics_consistent_with_policy": True,
            "blocker_anomaly_count": 0,
            "affected_media_count": 3,
            "baseline_media_count": 3,
        },
    }

    pending = refresh_completion_claims(json.loads(json.dumps(summary)))
    assert pending["status"] == "completed_with_followup_required"
    assert pending["pipeline_contract"]["claims"]["target_met"] is False

    summary["remaining_run"] = {"run_id": 8}
    summary["launcher_web_admin_acceptance"] = {
        "validated": True,
        "status": "passed_gui_execute_completed",
        "execute_clicked": True,
        "gui_execute_completed": True,
        "gui_execute_run_id": 9,
    }
    completed = refresh_completion_claims(summary)

    assert completed["status"] == "target_met"
    assert completed["standard_pipeline_flow"]["status"] == "completed"
    assert completed["standard_pipeline_flow"]["future_automation_readiness"] == "manual_pipeline_standardized_no_automatic_sync_implemented"
    assert completed["gpu_telemetry"]["actual_provider"] == "DmlExecutionProvider"
    assert completed["gpu_telemetry"]["validation_status"] == "passed"
    assert completed["pipeline_contract"]["claims"]["target_met"] is True


def test_s3a_m2_standard_pipeline_records_runner_fallback_without_gui_execute_claim() -> None:
    summary = {
        "pipeline_contract": {
            "contract_id": "s3a_m2_production_delta_e2e_contract_v1",
            "fresh_dry_run_completed": True,
        },
        "controlled_delta": {"cap": 1000},
        "dry_run": {"total_seen": 12, "partial_scan": False, "state_counts": {"skipped_placeholder": 0}},
        "execute": {"status": "completed", "imported": 5},
        "classification": {"reported": True, "count": 5, "failed": 0},
        "ai_tagging": {
            "reported": True,
            "count": 5,
            "failed": 0,
            "mature_media_tag_policy": True,
            "proper_nouns_suggestion_only": False,
            "no_sourceconcept_or_entity_truth_from_ai_only_tags": True,
        },
        "localization": {"status": "completed", "failed": 0},
        "localization_diagnosis": {
            "diagnosis": "benign_all_localizable_tags_already_localized_or_newly_localized",
            "tags_requiring_localization_after_runner": 0,
        },
        "placeholder_hydration": {
            "status": "completed",
            "remaining_placeholders_after_hydration": 0,
            "source_content_written": False,
            "source_deleted_moved_renamed": False,
        },
        "final_inventory": {
            "current_delta_candidates": 12,
            "current_importable_hydrated_supported_items": 0,
            "placeholders_remaining": 0,
            "scan_cap_stopped_scan": False,
        },
        "ledger_consistency": {"passed": True, "expected_plan_items": 12, "run_item_count": 12},
        "gpu_telemetry": {"status": "collected", "validation_status": "passed", "actual_provider": "DmlExecutionProvider"},
        "public_redaction": {"passed": True, "finding_count": 0},
        "public_reports": {
            "markdown_report_path": "docs/reports/s3a-m2-production-delta-e2e.md",
            "summary_json_path": "docs/reports/s3a-m2-production-delta-e2e-summary.json",
        },
        "safety": {"private_paths_or_hashes_in_public_report": False},
        "launcher_web_admin_acceptance": {
            "validated": True,
            "status": "passed_gui_execute_not_safe_runner_execute_used",
            "execute_clicked": False,
            "fallback_reason": "GUI cannot execute source-delta private plan under telemetry wrapper.",
            "computer_use_result": "policy_stop_then_browser_validation",
        },
    }

    flow = build_standard_pipeline_flow(summary)

    assert flow["status"] == "incomplete"
    step = flow["steps"]["validate_launcher_web_admin_workflow"]
    assert step["status"] == "gui_execute_pending_fallback_documented"
    assert step["completed"] is False
    assert step["evidence"]["execute_clicked"] is False


def test_s3a_m2_completion_claim_rejects_stale_gui_run_id() -> None:
    summary = {
        "pipeline_contract": {"contract_id": "s3a_m2_production_delta_e2e_contract_v1", "fresh_dry_run_completed": True},
        "production_acceptance": {"performed": True},
        "controlled_delta": {"cap_exceeded": False},
        "dry_run": {"total_seen": 3, "partial_scan": False, "state_counts": {"skipped_placeholder": 0}},
        "readiness": {"passed": True},
        "ledger_consistency": {"passed": True},
        "public_redaction": {"passed": True, "finding_count": 0},
        "public_reports": {
            "markdown_report_path": "docs/reports/s3a-m2-production-delta-e2e.md",
            "summary_json_path": "docs/reports/s3a-m2-production-delta-e2e-summary.json",
        },
        "safety": {"private_paths_or_hashes_in_public_report": False},
        "localization": {"status": "completed", "failed": 0},
        "localization_diagnosis": {
            "diagnosis": "benign_all_localizable_tags_already_localized_or_newly_localized",
            "tags_requiring_localization_after_runner": 0,
        },
        "placeholder_hydration": {
            "status": "completed",
            "remaining_placeholders_after_hydration": 0,
            "source_content_written": False,
            "source_deleted_moved_renamed": False,
        },
        "final_inventory": {"current_importable_hydrated_supported_items": 0, "placeholders_remaining": 0},
        "execute": {"status": "completed", "imported": 3, "classified": 3, "ai_tagged": 3},
        "remaining_run": {"run_id": 8},
        "classification": {"reported": True, "count": 3, "failed": 0},
        "ai_tagging": {
            "reported": True,
            "count": 3,
            "failed": 0,
            "mature_media_tag_policy": True,
            "proper_nouns_suggestion_only": False,
            "no_sourceconcept_or_entity_truth_from_ai_only_tags": True,
        },
        "gpu_telemetry": {"status": "collected", "validation_status": "passed", "actual_provider": "DmlExecutionProvider"},
        "launcher_web_admin_acceptance": {
            "validated": True,
            "status": "passed_gui_execute_completed",
            "execute_clicked": True,
            "gui_execute_completed": True,
            "gui_execute_run_id": 8,
        },
        "ai_tag_assignment_incident": {
            "status": "repaired",
            "public_safe": True,
            "after": {
                "all_ai_assignments_are_suggestions": False,
                "high_conf_nonproper_expected_normal_count": 2,
                "high_conf_nonproper_incorrect_suggestion_count": 0,
                "high_conf_nonproper_normal_count": 2,
                "high_conf_proper_expected_normal_count": 1,
                "high_conf_proper_incorrect_suggestion_count": 0,
                "high_conf_proper_normal_count": 1,
                "proper_noun_non_suggestion_count": 1,
            },
            "entity_truth_violations_found": 0,
            "localization_remaining_gap": 0,
            "ui_verification": {"status": "passed", "public_safe": True, "sample_count": 1, "normal_visible_pass_count": 1},
        },
        "cohort_self_audit": {
            "status": "passed_after_repair",
            "public_safe": True,
            "normal_ai_tag_semantics_consistent_with_policy": True,
            "blocker_anomaly_count": 0,
            "affected_media_count": 3,
            "baseline_media_count": 3,
        },
    }

    refreshed = refresh_completion_claims(summary)

    assert refreshed["pipeline_contract"]["claims"]["target_met"] is False
    assert refreshed["standard_pipeline_flow"]["steps"]["validate_launcher_web_admin_workflow"]["completed"] is False


def test_s3a_m2_ledger_pending_plan_uses_pending_delta_and_redacts_private_fields(tmp_path: Path) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    db = Session()
    try:
        source_root = tmp_path / "library"
        source_root.mkdir()
        root = DynamicSourceRoot(
            label="test",
            root_path=str(source_root),
            root_path_hash="abc1234567890defabc1234567890def",
            is_active=True,
        )
        db.add(root)
        db.flush()
        db.add(
            Media(
                filename="existing.png",
                path="media/original/existing.png",
                hash="hash-existing",
                file_type=FileTypeEnum.image,
            )
        )
        db.flush()
        db.add_all(
            [
                DynamicSourceItem(
                    source_root_id=root.id,
                    relative_path="new/image-1.png",
                    relative_path_hash="relhash-one",
                    file_size=123,
                    mtime_ns=1,
                    content_hash="hash-new",
                    source_status="available",
                    sync_state="new",
                    import_status="pending",
                ),
                DynamicSourceItem(
                    source_root_id=root.id,
                    relative_path="new/image-2.png",
                    relative_path_hash="relhash-two",
                    file_size=456,
                    mtime_ns=2,
                    content_hash="hash-existing",
                    source_status="available",
                    sync_state="new",
                    import_status="pending",
                ),
                DynamicSourceItem(
                    source_root_id=root.id,
                    relative_path="deferred/video.mov",
                    relative_path_hash="relhash-three",
                    source_status="deferred",
                    sync_state="skipped_unsupported",
                    import_status="deferred",
                    deferred_reason="unsupported_extension",
                ),
            ]
        )
        db.commit()

        args = SimpleNamespace(
            delta_cap=300,
            hydrated_only=True,
            stable_age_seconds=0.0,
            execute=False,
            plan_created_at="",
        )
        plan = build_ledger_pending_plan(db, args, root, include_private_details=False)

        counts = plan["counts"]
        assert counts["total_seen"] == 2
        assert counts["estimated_import_count"] == 1
        assert counts["state_counts"]["import_planned"] == 1
        assert counts["state_counts"]["skipped_existing_media"] == 1
        assert counts["source_ledger_aggregate_counts"]["sync_state_counts"]["skipped_unsupported"] == 1
        assert "private_details" not in plan
        public_json = json.dumps(plan, sort_keys=True)
        assert "new/image-1.png" not in public_json
        assert "hash-new" not in public_json
        assert "hash-existing" not in public_json
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_s3a_m2_source_delta_plan_caps_delta_not_unchanged_known_files(tmp_path: Path) -> None:
    from PIL import Image
    from app.services.dynamic_library_sync_service import _hash_text

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    db = Session()
    try:
        source_root = tmp_path / "library"
        source_root.mkdir()
        unchanged = source_root / "known.png"
        new_file = source_root / "fresh.png"
        Image.new("RGB", (2, 2), (1, 2, 3)).save(unchanged)
        Image.new("RGB", (2, 2), (4, 5, 6)).save(new_file)
        unchanged_stat = unchanged.stat()
        root = DynamicSourceRoot(
            label="test",
            root_path=str(source_root),
            root_path_hash="abc1234567890defabc1234567890def",
            is_active=True,
        )
        db.add(root)
        db.flush()
        media = Media(
            filename="known.png",
            path="media/original/known.png",
            hash="known-hash",
            file_type=FileTypeEnum.image,
        )
        db.add(media)
        db.flush()
        db.add(
            DynamicSourceItem(
                source_root_id=root.id,
                relative_path="known.png",
                relative_path_hash=_hash_text("known.png"),
                file_size=unchanged_stat.st_size,
                mtime_ns=unchanged_stat.st_mtime_ns,
                content_hash="known-hash",
                source_status="available",
                sync_state="new",
                import_status="imported",
                media_id=media.id,
            )
        )
        db.commit()

        args = SimpleNamespace(
            delta_cap=300,
            hydrated_only=True,
            stable_age_seconds=0.0,
            execute=False,
            plan_created_at="",
        )
        plan = build_source_delta_plan(db, args, root, include_private_details=False)

        counts = plan["counts"]
        assert counts["total_seen"] == 1
        assert counts["estimated_import_count"] == 1
        assert counts["state_counts"]["import_planned"] == 1
        assert plan["limits"]["scanned_files"] == 2
        assert plan["limits"]["unchanged_known_files"] == 1
        public_json = json.dumps(plan, sort_keys=True)
        assert "fresh.png" not in public_json
        assert "known.png" not in public_json
        assert "known-hash" not in public_json
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_s3a_m2_source_delta_reincludes_imported_item_with_missing_media_link(tmp_path: Path) -> None:
    from PIL import Image
    from app.services.dynamic_library_sync_service import _hash_text

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    db = Session()
    try:
        source_root = tmp_path / "library"
        source_root.mkdir()
        source_file = source_root / "orphaned-import.png"
        Image.new("RGB", (2, 2), (8, 8, 8)).save(source_file)
        stat = source_file.stat()
        rel_hash = _hash_text("orphaned-import.png")
        root = DynamicSourceRoot(
            label="test",
            root_path=str(source_root),
            root_path_hash="abc1234567890defabc1234567890def",
            is_active=True,
        )
        db.add(root)
        db.flush()
        db.add(
            DynamicSourceItem(
                source_root_id=root.id,
                relative_path="orphaned-import.png",
                relative_path_hash=rel_hash,
                file_size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                content_hash="previous-hash",
                source_status="available",
                sync_state="imported",
                import_status="imported",
                media_id=None,
            )
        )
        db.commit()

        args = SimpleNamespace(
            delta_cap=300,
            hydrated_only=True,
            stable_age_seconds=0.0,
            execute=False,
            plan_created_at="",
        )
        plan = build_source_delta_plan(db, args, root, include_private_details=False)

        counts = plan["counts"]
        assert counts["total_seen"] == 1
        assert counts["estimated_import_count"] == 1
        assert counts["state_counts"]["import_planned"] == 1
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_s3a_m2_source_delta_reincludes_unresolved_placeholder_from_run(tmp_path: Path) -> None:
    from PIL import Image

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    db = Session()
    try:
        source_root = tmp_path / "library"
        source_root.mkdir()
        placeholder_now_hydrated = source_root / "hydrated.png"
        Image.new("RGB", (2, 2), (7, 8, 9)).save(placeholder_now_hydrated)
        stat = placeholder_now_hydrated.stat()
        root = DynamicSourceRoot(
            label="test",
            root_path=str(source_root),
            root_path_hash="abc1234567890defabc1234567890def",
            is_active=True,
        )
        db.add(root)
        db.flush()
        db.add(DynamicSyncRun(id=7, run_type="manual_sync_execute", mode="production_acceptance", status="completed"))
        db.add(
            DynamicSourceItem(
                source_root_id=root.id,
                relative_path="hydrated.png",
                relative_path_hash=hashlib.sha256("hydrated.png".encode("utf-8")).hexdigest(),
                file_size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                source_status="available",
                sync_state="skipped_placeholder",
                import_status="deferred",
                deferred_reason="cloud_placeholder",
                last_sync_run_id=7,
            )
        )
        db.commit()

        args = SimpleNamespace(
            delta_cap=300,
            hydrated_only=True,
            stable_age_seconds=0.0,
            execute=False,
            plan_created_at="",
            include_unresolved_run_id=7,
        )
        plan = build_source_delta_plan(db, args, root, include_private_details=False)

        counts = plan["counts"]
        assert counts["total_seen"] == 1
        assert counts["state_counts"]["import_planned"] == 1
        assert counts["estimated_import_count"] == 1
        assert plan["limits"]["unchanged_known_files"] == 0
        assert "hydrated.png" not in json.dumps(plan, sort_keys=True)
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_s3a_m2_placeholder_rows_from_private_plan_selects_only_placeholders() -> None:
    plan = {
        "private_details": {
            "items": [
                {"safe_label": "delta-1", "relative_path": "a.png", "relative_path_hash": "hash-a", "state": "skipped_placeholder", "reason": "cloud_placeholder"},
                {"safe_label": "delta-2", "relative_path": "b.png", "relative_path_hash": "hash-b", "state": "import_planned", "reason": None},
            ],
            "not_for_public_reports": True,
        }
    }

    rows = _placeholder_rows_from_plan(plan)

    assert len(rows) == 1
    assert rows[0]["safe_label"] == "delta-1"
    assert rows[0]["relative_path"] == "a.png"
