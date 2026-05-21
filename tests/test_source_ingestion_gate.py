from pathlib import Path

from app.services import source_ingestion_gate
from app.services.source_ingestion_gate import SourceIngestionGate
from app.utils import cloud_files
from app.utils.cloud_files import CloudFileState


def test_path_source_blocks_cloud_risk(monkeypatch, tmp_path: Path):
    source = tmp_path / "cloud.jpg"

    monkeypatch.setattr(
        source_ingestion_gate,
        "classify_cloud_file_state",
        lambda path: CloudFileState(
            path=str(path),
            supported_platform=True,
            exists=True,
            is_file=True,
            offline=True,
            reparse_point=True,
            likely_cloud_placeholder=True,
        ),
    )

    result = SourceIngestionGate.evaluate_path_source(source, safe_label="source_row_0001.jpg")

    assert result.allowed is False
    assert result.blocked is True
    assert result.source_kind == "path_source"
    assert result.reason == "cloud_offline"
    assert result.required_policy == "controlled_hydration_or_read_probe_or_backfill"
    public = result.to_public_dict()
    assert "path" not in public["cloud_state"]
    assert str(tmp_path) not in str(public)


def test_path_source_allows_no_cloud_risk(monkeypatch, tmp_path: Path):
    source = tmp_path / "local.jpg"

    monkeypatch.setattr(
        source_ingestion_gate,
        "classify_cloud_file_state",
        lambda path: CloudFileState(
            path=str(path),
            supported_platform=True,
            exists=True,
            is_file=True,
        ),
    )

    result = SourceIngestionGate.evaluate_path_source(source)

    assert result.allowed is True
    assert result.blocked is False
    assert result.reason == "path_source_available"


def test_upload_bytes_skips_source_cloud_gate():
    result = SourceIngestionGate.allow_upload_bytes(safe_label="upload_request")

    assert result.allowed is True
    assert result.source_kind == "upload_bytes"
    assert result.cloud_state is None
    assert result.reason == "upload_bytes_already_supplied"


def test_app_managed_file_skips_source_cloud_gate():
    result = SourceIngestionGate.allow_app_managed_file(safe_label="media_original")

    assert result.allowed is True
    assert result.source_kind == "app_managed_file"
    assert result.cloud_state is None
    assert result.reason == "app_managed_storage_consistency_applies"


def test_staging_file_requires_passed_audit():
    blocked = SourceIngestionGate.evaluate_staging_file(
        staging_audit_passed=False,
        safe_label="phase_staging",
    )
    allowed = SourceIngestionGate.evaluate_staging_file(
        staging_audit_passed=True,
        safe_label="phase_staging",
    )

    assert blocked.blocked is True
    assert blocked.reason == "staging_audit_required"
    assert allowed.allowed is True
    assert allowed.reason == "staging_audit_passed"


def test_non_windows_no_risk_behavior(monkeypatch, tmp_path: Path):
    source = tmp_path / "local.jpg"
    source.write_bytes(b"x")
    monkeypatch.setattr(cloud_files, "IS_WINDOWS", False)

    result = SourceIngestionGate.evaluate_path_source(source)

    assert result.allowed is True
    assert result.cloud_state.supported_platform is False
