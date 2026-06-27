from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

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
from app.models import DynamicSourceItem, DynamicSourceRoot, Media
from scripts.run_s3a_m2_delta_e2e_with_telemetry import (
    build_ledger_pending_plan,
    build_source_delta_plan,
    refresh_completion_claims,
    s3a_m2_approval_phrase,
    summarize_telemetry,
)


def test_s3a_m2_approval_phrase_is_plan_hash_bound() -> None:
    phrase = s3a_m2_approval_phrase("abcdef1234567890")

    assert phrase == "I APPROVE S3A-M2 PRODUCTION DELTA E2E abcdef123456"


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


def test_s3a_m2_completion_claim_requires_launcher_validation() -> None:
    summary = {
        "status": "completed_with_followup_required",
        "pipeline_contract": {"claims": {"target_met": False}},
        "production_acceptance": {"performed": True},
        "controlled_delta": {"cap_exceeded": False},
        "readiness": {"passed": True},
        "ledger_consistency": {"passed": True},
        "localization": {"status": "completed", "failed": 0},
        "execute": {
            "status": "completed",
            "imported": 3,
            "provider_provenance": {"provider": {"actual_provider": "DmlExecutionProvider"}},
        },
        "gpu_telemetry": {"status": "collected"},
        "launcher_web_admin_acceptance": {"validated": False, "status": "pending"},
    }

    pending = refresh_completion_claims(json.loads(json.dumps(summary)))
    assert pending["status"] == "completed_with_followup_required"
    assert pending["pipeline_contract"]["claims"]["target_met"] is False

    summary["launcher_web_admin_acceptance"] = {"validated": True, "status": "passed"}
    completed = refresh_completion_claims(summary)

    assert completed["status"] == "target_met"
    assert completed["gpu_telemetry"]["actual_provider"] == "DmlExecutionProvider"
    assert completed["gpu_telemetry"]["validation_status"] == "passed"
    assert completed["pipeline_contract"]["claims"]["target_met"] is True


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
        db.add(
            DynamicSourceItem(
                source_root_id=root.id,
                relative_path="known.png",
                relative_path_hash=hashlib.sha256("known.png".encode("utf-8")).hexdigest(),
                file_size=unchanged_stat.st_size,
                mtime_ns=unchanged_stat.st_mtime_ns,
                content_hash="known-hash",
                source_status="available",
                sync_state="new",
                import_status="imported",
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
