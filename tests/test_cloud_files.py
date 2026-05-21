from pathlib import Path
import pytest

from app.utils import cloud_files
from app.utils.cloud_files import (
    CloudFileState,
    FILE_ATTRIBUTE_ARCHIVE,
    FILE_ATTRIBUTE_OFFLINE,
    FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS,
    FILE_ATTRIBUTE_RECALL_ON_OPEN,
    FILE_ATTRIBUTE_REPARSE_POINT,
    FILE_ATTRIBUTE_SPARSE_FILE,
    classify_cloud_file_state,
    classify_file_access_error,
)


def test_windows_cloud_attributes_detect_recall_and_offline(monkeypatch, tmp_path: Path):
    source = tmp_path / "cloud.jpg"
    raw = (
        FILE_ATTRIBUTE_ARCHIVE
        | FILE_ATTRIBUTE_OFFLINE
        | FILE_ATTRIBUTE_REPARSE_POINT
        | FILE_ATTRIBUTE_SPARSE_FILE
        | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
    )
    monkeypatch.setattr(cloud_files, "IS_WINDOWS", True)
    monkeypatch.setattr(cloud_files, "_get_windows_attributes_raw", lambda path: (raw, None, None))

    state = classify_cloud_file_state(source)

    assert state.supported_platform is True
    assert state.exists is True
    assert state.is_file is True
    assert state.offline is True
    assert state.reparse_point is True
    assert state.sparse_file is True
    assert state.recall_on_data_access is True
    assert state.likely_cloud_placeholder is True


def test_windows_recall_on_open_is_cloud_placeholder(monkeypatch, tmp_path: Path):
    source = tmp_path / "cloud.jpg"
    monkeypatch.setattr(cloud_files, "IS_WINDOWS", True)
    monkeypatch.setattr(
        cloud_files,
        "_get_windows_attributes_raw",
        lambda path: (FILE_ATTRIBUTE_ARCHIVE | FILE_ATTRIBUTE_RECALL_ON_OPEN, None, None),
    )

    state = classify_cloud_file_state(source)

    assert state.recall_on_open is True
    assert state.likely_cloud_placeholder is True


def test_non_windows_safe_behavior(monkeypatch, tmp_path: Path):
    source = tmp_path / "local.jpg"
    source.write_bytes(b"x")
    monkeypatch.setattr(cloud_files, "IS_WINDOWS", False)

    state = classify_cloud_file_state(source)

    assert state.supported_platform is False
    assert state.exists is True
    assert state.is_file is True
    assert state.likely_cloud_placeholder is False


def test_attribute_error_is_structured(monkeypatch, tmp_path: Path):
    source = tmp_path / "missing.jpg"
    monkeypatch.setattr(cloud_files, "IS_WINDOWS", True)
    monkeypatch.setattr(cloud_files, "_get_windows_attributes_raw", lambda path: (None, 2, "file not found"))

    state = classify_cloud_file_state(source)

    assert state.supported_platform is True
    assert state.exists is False
    assert state.error_code == 2
    assert state.error_message == "file not found"


def test_winerror_388_is_cloud_network_unavailable():
    class FakeCopyError(OSError):
        winerror = 388
        errno = None

    assert classify_file_access_error(FakeCopyError("copy failed")) == "cloud_network_unavailable"


def test_cloud_state_drives_hydration_failure_reason():
    state = CloudFileState(
        path="x",
        supported_platform=True,
        exists=True,
        is_file=True,
        recall_on_data_access=True,
        likely_cloud_placeholder=True,
    )

    assert classify_file_access_error(OSError("copy failed"), state) == "cloud_recall_on_data_access"
