"""Unit tests for Phase 2.4 iCloud / large library safety features."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.utils.local_library_scanner import (
    _is_cloud_only,
    _is_hidden,
    _is_scannable_file,
    _map_skip_reason,
    _SKIP_REASON_STAT_MAP,
    preflight_analyze,
)


@pytest.fixture
def tmp_scan_dir(tmp_path):
    """Create a temp directory with a few test images."""
    for name in ["a.jpg", "b.png", "c.webp"]:
        (tmp_path / name).write_bytes(b"\xff\xd8" + b"\x00" * 100)
    return tmp_path


class TestIsScannableFile:

    def test_regular_jpg(self, tmp_path):
        f = tmp_path / "test.jpg"
        f.write_bytes(b"\xff\xd8" + b"\x00" * 100)
        assert _is_scannable_file(f) is None

    def test_dotfile_hidden(self, tmp_path):
        f = tmp_path / ".hidden.jpg"
        f.write_bytes(b"\xff\xd8" + b"\x00" * 100)
        assert _is_scannable_file(f) == "hidden"

    def test_icloud_extension(self, tmp_path):
        f = tmp_path / "photo.icloud"
        f.write_bytes(b"stub")
        assert _is_scannable_file(f) == "icloud_placeholder"

    def test_unsupported_extension(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF" + b"\x00" * 100)
        assert _is_scannable_file(f) == "unsupported_extension"

    def test_zero_byte(self, tmp_path):
        f = tmp_path / "empty.jpg"
        f.write_bytes(b"")
        assert _is_scannable_file(f) == "zero_byte_file"

    @patch("app.utils.local_library_scanner.settings")
    def test_too_large(self, mock_settings, tmp_path):
        mock_settings.SCAN_MAX_FILE_SIZE_MB = 1
        f = tmp_path / "big.jpg"
        f.write_bytes(b"\xff" * (2 * 1024 * 1024))
        assert _is_scannable_file(f) == "too_large"

    @patch("app.utils.local_library_scanner._is_cloud_only", return_value=True)
    def test_cloud_only_skipped_when_hydrated_only(self, mock_cloud, tmp_path):
        f = tmp_path / "cloud.jpg"
        f.write_bytes(b"\xff\xd8" + b"\x00" * 100)
        assert _is_scannable_file(f, hydrated_only=True) == "cloud_placeholder"

    @patch("app.utils.local_library_scanner._is_cloud_only", return_value=True)
    def test_cloud_only_allowed_when_not_hydrated_only(self, mock_cloud, tmp_path):
        f = tmp_path / "cloud.jpg"
        f.write_bytes(b"\xff\xd8" + b"\x00" * 100)
        assert _is_scannable_file(f, hydrated_only=False) is None

    def test_symlink_skipped(self, tmp_path):
        target = tmp_path / "real.jpg"
        target.write_bytes(b"\xff\xd8" + b"\x00" * 100)
        link = tmp_path / "link.jpg"
        try:
            link.symlink_to(target)
        except OSError:
            pytest.skip("Symlinks require elevated privileges on Windows")
        assert _is_scannable_file(link) == "symlink"


class TestMapSkipReason:

    def test_known_reasons(self):
        for reason, key in _SKIP_REASON_STAT_MAP.items():
            stats = {k: 0 for k in [
                "skipped_cloud_placeholder", "skipped_zero_byte",
                "skipped_hidden", "skipped_too_large",
                "skipped_unreadable", "skipped_unsupported",
            ]}
            _map_skip_reason(stats, reason)
            assert stats[key] == 1

    def test_stat_error_maps_to_unreadable(self):
        stats = {"skipped_unreadable": 0, "skipped_unsupported": 0}
        _map_skip_reason(stats, "stat_error: permission denied")
        assert stats["skipped_unreadable"] == 1
        assert stats["skipped_unsupported"] == 0

    def test_unknown_maps_to_unsupported(self):
        stats = {"skipped_unsupported": 0}
        _map_skip_reason(stats, "unsupported_extension")
        assert stats["skipped_unsupported"] == 1


class TestIsHidden:

    def test_dotfile(self, tmp_path):
        f = tmp_path / ".secret"
        f.write_bytes(b"x")
        assert _is_hidden(f) is True

    def test_normal_file(self, tmp_path):
        f = tmp_path / "normal.txt"
        f.write_bytes(b"x")
        assert _is_hidden(f) is False


class TestIsCloudOnly:

    def test_non_windows_returns_false(self, tmp_path):
        f = tmp_path / "test.jpg"
        f.write_bytes(b"x")
        with patch("app.utils.local_library_scanner._IS_WINDOWS", False):
            assert _is_cloud_only(f) is False


class TestPreflightAnalyze:

    @patch("app.utils.local_library_scanner.settings")
    def test_counts_files(self, mock_settings, tmp_scan_dir):
        mock_settings.SCAN_MAX_FILE_SIZE_MB = 200
        result = preflight_analyze([tmp_scan_dir])
        assert result["processed"] == 3
        assert result["imported"] == 3
        assert result["estimated_size_bytes"] > 0
        assert result["largest_file_bytes"] > 0
        assert ".jpg" in result["extensions"]

    @patch("app.utils.local_library_scanner.settings")
    def test_max_files_limit(self, mock_settings, tmp_scan_dir):
        mock_settings.SCAN_MAX_FILE_SIZE_MB = 200
        result = preflight_analyze([tmp_scan_dir], max_files=1)
        assert result["processed"] <= 1
        assert result["limit_reached"] is True

    @patch("app.utils.local_library_scanner.settings")
    def test_cancel_check(self, mock_settings, tmp_scan_dir):
        mock_settings.SCAN_MAX_FILE_SIZE_MB = 200
        cancel = MagicMock(side_effect=[False, True])
        result = preflight_analyze([tmp_scan_dir], cancel_check=cancel)
        assert result["total_seen"] < 10

    @patch("app.utils.local_library_scanner.settings")
    def test_nonexistent_path(self, mock_settings, tmp_path):
        mock_settings.SCAN_MAX_FILE_SIZE_MB = 200
        fake = tmp_path / "nope"
        result = preflight_analyze([fake])
        assert result["failed"] == 1

    @patch("app.utils.local_library_scanner.settings")
    def test_hidden_files_skipped(self, mock_settings, tmp_path):
        mock_settings.SCAN_MAX_FILE_SIZE_MB = 200
        (tmp_path / ".hidden.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 100)
        (tmp_path / "visible.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 100)
        result = preflight_analyze([tmp_path])
        assert result["skipped_hidden"] == 1
        assert result["processed"] == 1

    @patch("app.utils.local_library_scanner.settings")
    def test_progress_callback_called(self, mock_settings, tmp_scan_dir):
        mock_settings.SCAN_MAX_FILE_SIZE_MB = 200
        cb = MagicMock()
        preflight_analyze([tmp_scan_dir], progress_callback=cb)
        assert cb.call_count >= 1

    @patch("app.utils.local_library_scanner.settings")
    def test_extensions_breakdown(self, mock_settings, tmp_scan_dir):
        mock_settings.SCAN_MAX_FILE_SIZE_MB = 200
        result = preflight_analyze([tmp_scan_dir])
        exts = result["extensions"]
        assert sum(exts.values()) == 3
        assert ".jpg" in exts and ".png" in exts and ".webp" in exts


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
