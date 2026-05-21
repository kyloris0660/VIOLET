import importlib.util
import sys
from pathlib import Path

from app.services.source_ingestion_gate import SourceIngestionGateResult
from app.utils import cloud_files
from app.utils.cloud_files import CloudFileState, read_verify_full_content


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "run_phase38d_i5_hydration_audit.py"
_spec = importlib.util.spec_from_file_location("run_phase38d_i5_hydration_audit", SCRIPT_PATH)
i5 = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["run_phase38d_i5_hydration_audit"] = i5
_spec.loader.exec_module(i5)


def _row(row_id: int, tmp_path: Path, *, bucket: str = "b01", selected: bool = True, ext: str = ".jpg") -> dict[str, str]:
    source = tmp_path / f"source_{row_id}{ext}"
    target = tmp_path / "target" / f"target_{row_id}{ext}"
    source.write_bytes((f"row-{row_id}").encode("ascii"))
    return {
        "row_id": str(row_id),
        "source_path": str(source),
        "proposed_target_path": str(target),
        "extension": ext,
        "size_bytes": str(source.stat().st_size),
        "selection_reason": "new_candidate" if selected else "",
        "duplicate_key": "",
        "exclusion_reason": "" if selected else "not_selected_temporal_stratified",
        "placeholder_flag": "False",
        "stat_error": "False",
        "temporal_bucket": bucket,
        "timestamp_source": "filesystem_mtime",
        "modified_time_utc": "2026-01-01T00:00:00+00:00",
    }


def _fake_gate(*, risky_ids: set[int] | None = None):
    risky_ids = risky_ids or set()

    def fake(path, safe_label=None, hydration_policy_enabled=False):
        row_id = int(str(safe_label).split("_row_", 1)[1].split(".", 1)[0])
        risky = row_id in risky_ids
        state = CloudFileState(
            path=str(path),
            supported_platform=True,
            exists=True,
            is_file=True,
            recall_on_data_access=risky,
            likely_cloud_placeholder=risky,
        )
        return SourceIngestionGateResult(
            allowed=not risky or hydration_policy_enabled,
            blocked=risky and not hydration_policy_enabled,
            source_kind="path_source",
            reason="cloud_recall_on_data_access" if risky else "path_source_available",
            required_policy="controlled_hydration_or_read_probe_or_backfill" if risky else None,
            cloud_state=state,
            safe_label=safe_label,
        )

    return fake


def test_metadata_only_does_not_read_file_content(monkeypatch, tmp_path: Path):
    rows = [_row(98, tmp_path, bucket="b02"), _row(99, tmp_path, bucket="b02", ext=".png")]
    monkeypatch.setattr(i5.SourceIngestionGate, "evaluate_path_source", _fake_gate(risky_ids={98, 99}))
    monkeypatch.setattr(i5, "read_probe_prefix", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("read probe should not run")))
    monkeypatch.setattr(i5, "read_verify_full_content", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("full read should not run")))

    report, _local = i5.run_hydration_audit(
        rows,
        failed_row_id=98,
        stop_after="metadata",
        sample_per_bucket=3,
        max_sample=48,
        policy={
            "prefix_bytes": 1,
            "prefix_timeout_seconds": 10,
            "prefix_retries": 1,
            "full_timeout_seconds": 60,
            "full_retries": 1,
            "full_chunk_size": 1024,
        },
    )

    assert report["metadata_baseline"]["selected_total"] == 2
    assert report["metadata_baseline"]["likely_cloud_placeholder_count"] == 2
    assert report["sample_gate"]["status"] == "not_requested"
    assert report["safety"]["source_content_read_for_verification_only"] is False


def test_full_read_verification_reads_all_bytes(tmp_path: Path):
    source = tmp_path / "full.jpg"
    source.write_bytes(b"abcdef")

    result = read_verify_full_content(source, expected_size=6, timeout_seconds=10, retries=0, chunk_size=2)

    assert result["ok"] is True
    assert result["bytes_read"] == 6
    assert result["bytes_read_total"] == 6


def test_full_read_rejects_zero_chunk_size_without_false_positive(tmp_path: Path):
    source = tmp_path / "full.jpg"
    source.write_bytes(b"abcdef")

    result = read_verify_full_content(source, expected_size=None, timeout_seconds=10, retries=0, chunk_size=0)

    assert result["ok"] is False
    assert result["error_reason"] == "invalid_chunk_size"
    assert result["bytes_read"] == 0
    assert result["attempts"] == []


def test_full_read_rejects_negative_chunk_size(tmp_path: Path):
    source = tmp_path / "full.jpg"
    source.write_bytes(b"abcdef")

    result = read_verify_full_content(source, expected_size=6, timeout_seconds=10, retries=0, chunk_size=-1)

    assert result["ok"] is False
    assert result["error_reason"] == "invalid_chunk_size"


def test_full_read_size_mismatch_is_structured(tmp_path: Path):
    source = tmp_path / "full.jpg"
    source.write_bytes(b"abcdef")

    result = read_verify_full_content(source, expected_size=7, timeout_seconds=10, retries=0, chunk_size=2)

    assert result["ok"] is False
    assert result["error_reason"] == "size_mismatch"


def test_full_read_worker_eof_is_structured(monkeypatch, tmp_path: Path):
    source = tmp_path / "full.jpg"
    source.write_bytes(b"abcdef")

    class FakeParent:
        def poll(self, timeout=None):
            return True

        def recv(self):
            raise EOFError

        def close(self):
            pass

    class FakeChild:
        def close(self):
            pass

    class FakeProcess:
        exitcode = 17

        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def join(self, timeout=None):
            pass

        def is_alive(self):
            return False

    monkeypatch.setattr(cloud_files.multiprocessing, "Pipe", lambda duplex=False: (FakeParent(), FakeChild()))
    monkeypatch.setattr(cloud_files.multiprocessing, "Process", FakeProcess)

    result = read_verify_full_content(source, expected_size=6, timeout_seconds=10, retries=0, chunk_size=2)

    assert result["ok"] is False
    assert result["error_reason"] == "read_worker_eof"
    assert "17" in result["error_message"]


def test_row98_is_always_in_sample(monkeypatch, tmp_path: Path):
    rows = [_row(98, tmp_path, bucket="b02"), _row(99, tmp_path, bucket="b02"), _row(200, tmp_path, bucket="b03")]
    monkeypatch.setattr(i5.SourceIngestionGate, "evaluate_path_source", _fake_gate(risky_ids={98, 99, 200}))
    metadata, _local = i5.build_metadata_records(rows)

    sample = i5.select_sample_records(metadata, failed_row_id=98, sample_per_bucket=1, max_sample=2)

    assert [record["row_id"] for record in sample][0] == 98
    assert len(sample) == 2


def test_no_recall_risk_empty_sample_is_not_applicable(monkeypatch, tmp_path: Path):
    rows = [_row(98, tmp_path, bucket="b02"), _row(99, tmp_path, bucket="b02")]
    monkeypatch.setattr(i5.SourceIngestionGate, "evaluate_path_source", _fake_gate(risky_ids=set()))
    monkeypatch.setattr(i5, "read_probe_prefix", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("read probe should not run")))
    monkeypatch.setattr(i5, "read_verify_full_content", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("full read should not run")))

    report, _local = i5.run_hydration_audit(
        rows,
        failed_row_id=98,
        stop_after="full",
        sample_per_bucket=3,
        max_sample=48,
        policy={
            "prefix_bytes": 1,
            "prefix_timeout_seconds": 10,
            "prefix_retries": 1,
            "full_timeout_seconds": 60,
            "full_retries": 1,
            "full_chunk_size": 1024,
        },
    )

    assert report["metadata_baseline"]["likely_cloud_placeholder_count"] == 0
    assert report["sample_gate"]["status"] == "not_applicable_no_risk"
    assert report["full_recall_verification"]["status"] == "not_applicable_no_risk"
    assert report["safety"]["source_content_read_for_verification_only"] is False
    assert report["safety"]["provider_side_hydration_may_have_occurred"] is False


def test_risky_rows_with_empty_sample_selection_blocks(monkeypatch, tmp_path: Path):
    rows = [_row(98, tmp_path, bucket="b02"), _row(99, tmp_path, bucket="b02")]
    monkeypatch.setattr(i5.SourceIngestionGate, "evaluate_path_source", _fake_gate(risky_ids={98, 99}))
    monkeypatch.setattr(i5, "select_sample_records", lambda *args, **kwargs: [])
    monkeypatch.setattr(i5, "read_probe_prefix", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("read probe should not run")))
    monkeypatch.setattr(i5, "read_verify_full_content", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("full read should not run")))

    report, _local = i5.run_hydration_audit(
        rows,
        failed_row_id=98,
        stop_after="full",
        sample_per_bucket=3,
        max_sample=48,
        policy={
            "prefix_bytes": 1,
            "prefix_timeout_seconds": 10,
            "prefix_retries": 1,
            "full_timeout_seconds": 60,
            "full_retries": 1,
            "full_chunk_size": 1024,
        },
    )

    assert report["metadata_baseline"]["likely_cloud_placeholder_count"] == 2
    assert report["sample_gate"]["status"] == "blocked_empty_sample_selection"
    assert report["full_recall_verification"]["status"] == "blocked_empty_sample_selection"


def test_sample_gate_failure_stops_full_risk_set(monkeypatch, tmp_path: Path):
    rows = [_row(98, tmp_path, bucket="b02"), _row(99, tmp_path, bucket="b02")]
    monkeypatch.setattr(i5.SourceIngestionGate, "evaluate_path_source", _fake_gate(risky_ids={98, 99}))
    monkeypatch.setattr(i5, "read_probe_prefix", lambda *args, **kwargs: {"ok": False, "bytes_read": 0, "error_reason": "cloud_network_unavailable"})
    monkeypatch.setattr(i5, "read_verify_full_content", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("full read should be skipped")))

    report, _local = i5.run_hydration_audit(
        rows,
        failed_row_id=98,
        stop_after="full",
        sample_per_bucket=3,
        max_sample=48,
        policy={
            "prefix_bytes": 1,
            "prefix_timeout_seconds": 10,
            "prefix_retries": 1,
            "full_timeout_seconds": 60,
            "full_retries": 1,
            "full_chunk_size": 1024,
        },
    )

    assert report["sample_gate"]["status"] == "failed"
    assert report["full_recall_verification"]["status"] == "skipped_sample_gate_failed"
    assert report["backfill_plan"]["replacement_count"] == 0
    assert report["backfill_plan"]["unresolved_count"] == 2


def test_full_risk_set_report_schema_and_privacy(monkeypatch, tmp_path: Path):
    rows = [_row(98, tmp_path, bucket="b02"), _row(99, tmp_path, bucket="b02"), _row(100, tmp_path, bucket="b03")]
    hydrated: set[int] = set()

    def fake_gate(path, safe_label=None, hydration_policy_enabled=False):
        row_id = int(str(safe_label).split("_row_", 1)[1].split(".", 1)[0])
        risky = row_id in {98, 99, 100} and row_id not in hydrated
        state = CloudFileState(
            path=str(path),
            supported_platform=True,
            exists=True,
            is_file=True,
            recall_on_data_access=risky,
            likely_cloud_placeholder=risky,
        )
        return SourceIngestionGateResult(
            allowed=not risky or hydration_policy_enabled,
            blocked=risky and not hydration_policy_enabled,
            source_kind="path_source",
            reason="cloud_recall_on_data_access" if risky else "path_source_available",
            cloud_state=state,
            safe_label=safe_label,
        )

    def fake_prefix(path, **kwargs):
        return {"ok": True, "bytes_read": 1, "error_reason": None}

    def fake_full(path, expected_size=None, **kwargs):
        row_id = int(Path(path).stem.split("_", 1)[1])
        hydrated.add(row_id)
        return {
            "ok": True,
            "bytes_read": expected_size,
            "bytes_read_total": expected_size,
            "duration_seconds": 0.1,
            "error_reason": None,
        }

    monkeypatch.setattr(i5.SourceIngestionGate, "evaluate_path_source", fake_gate)
    monkeypatch.setattr(i5, "read_probe_prefix", fake_prefix)
    monkeypatch.setattr(i5, "read_verify_full_content", fake_full)

    report, local = i5.run_hydration_audit(
        rows,
        failed_row_id=98,
        stop_after="full",
        sample_per_bucket=1,
        max_sample=2,
        policy={
            "prefix_bytes": 1,
            "prefix_timeout_seconds": 10,
            "prefix_retries": 1,
            "full_timeout_seconds": 60,
            "full_retries": 1,
            "full_chunk_size": 1024,
        },
    )

    assert report["sample_gate"]["status"] == "passed"
    assert report["full_recall_verification"]["status"] == "completed"
    assert "attempted_count" in report["full_recall_verification"]["summary"]
    assert report["privacy"]["passed"] is True
    assert str(tmp_path) not in str(report)
    assert "source_98.jpg" in str(local)


def test_cloud_files_full_read_timeout_result_can_be_mocked(monkeypatch, tmp_path: Path):
    source = tmp_path / "slow.jpg"
    source.write_bytes(b"abc")

    monkeypatch.setattr(cloud_files.multiprocessing.Process, "start", lambda self: None)
    monkeypatch.setattr(cloud_files.multiprocessing.Process, "is_alive", lambda self: True)
    monkeypatch.setattr(cloud_files.multiprocessing.Process, "terminate", lambda self: None)
    monkeypatch.setattr(cloud_files.multiprocessing.Process, "kill", lambda self: None)
    monkeypatch.setattr(cloud_files.multiprocessing.Process, "join", lambda self, timeout=None: None)

    result = read_verify_full_content(source, expected_size=3, timeout_seconds=1, retries=0, chunk_size=1)

    assert result["ok"] is False
    assert result["error_reason"] == "read_timeout"
    assert len(result["attempts"]) == 1
