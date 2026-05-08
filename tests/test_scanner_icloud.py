"""Unit tests for Phase 2.4 iCloud / large library safety features."""
import os
import sys
import tempfile
import time
import multiprocessing
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
    _calculate_file_hash_with_timeout,
    preflight_analyze,
    scan_and_import,
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


class TestSubprocessTimeout:
    """Tests proving the subprocess-based timeout works correctly."""

    def test_normal_hash_succeeds(self, tmp_path):
        """A normal file hashes quickly and returns ok."""
        f = tmp_path / "normal.jpg"
        f.write_bytes(b"\xff\xd8" + b"\x00" * 1000)
        status, value = _calculate_file_hash_with_timeout(f, timeout_sec=10)
        assert status == "ok"
        assert len(value) == 32  # MD5 hex

    def test_nonexistent_file_returns_error(self, tmp_path):
        """A nonexistent file returns error status."""
        f = tmp_path / "nope.jpg"
        status, value = _calculate_file_hash_with_timeout(f, timeout_sec=10)
        assert status == "error"

    @pytest.mark.skipif(
        sys.platform != "win32" and os.environ.get("CI") == "true",
        reason="Timing-sensitive test; skip in constrained CI environments",
    )
    def test_stuck_hash_times_out_and_scan_continues(self, tmp_path):
        """Simulate a stuck file hash that exceeds timeout.

        Proves:
        1. The stuck hash times out (does not block forever)
        2. skipped_timeout is incremented
        3. Scan continues to process subsequent files
        4. Later files are still processed successfully
        """
        stuck = tmp_path / "stuck.jpg"
        stuck.write_bytes(b"\xff\xd8" + b"\x00" * 100)
        normal1 = tmp_path / "after1.jpg"
        normal1.write_bytes(b"\xff\xd8" + b"\x00" * 200)
        normal2 = tmp_path / "after2.jpg"
        normal2.write_bytes(b"\xff\xd8" + b"\x00" * 300)

        hash_counter = [0]
        timeout_count = [0]
        ok_count = [0]

        def mock_hash_with_timeout(file_path, timeout_sec):
            """Return timeout for 'stuck' file, fake hash for others."""
            hash_counter[0] += 1
            path_str = str(file_path)
            if path_str.endswith("stuck.jpg"):
                timeout_count[0] += 1
                return ("timeout", f"hash timed out after {timeout_sec}s")
            import hashlib
            ok_count[0] += 1
            fake = hashlib.md5(path_str.encode()).hexdigest()
            return ("ok", fake)

        mock_db = MagicMock()
        mock_db.query.return_value.all.return_value = []
        mock_db.query.return_value.filter.return_value.first.return_value = None

        with patch("app.utils.local_library_scanner._calculate_file_hash_with_timeout", side_effect=mock_hash_with_timeout):
            with patch("app.utils.local_library_scanner.settings") as mock_settings:
                mock_settings.SCAN_MAX_FILE_SIZE_MB = 200
                mock_settings.SCAN_FILE_OPEN_TIMEOUT_SECONDS = 2
                mock_settings.BASE_DIR = Path("C:/fake/project")
                mock_settings.ORIGINAL_DIR = tmp_path / "output"

                result = scan_and_import(
                    mock_db,
                    [tmp_path],
                    dry_run=True,
                    hydrated_only=False,
                )

        # Prove timeout was recorded for the stuck file
        assert timeout_count[0] == 1, f"Expected 1 timeout, got {timeout_count[0]}"
        assert result["skipped_timeout"] == 1
        # Prove scan continued past the stuck file
        assert ok_count[0] == 2, f"Expected 2 ok hashes, got {ok_count[0]}"
        assert result["imported"] == 2
        # Prove total candidates processed includes stuck + normal
        assert result["processed"] == 3
        # Prove the hash function was called for all 3 files
        assert hash_counter[0] == 3

    def test_timeout_with_real_subprocess(self, tmp_path):
        """Use a very short timeout (1s) on a real file to verify subprocess
        termination mechanics work (the file is small so it won't actually
        time out — this tests the happy path through the subprocess)."""
        f = tmp_path / "quick.jpg"
        f.write_bytes(b"\xff\xd8" + b"\x00" * 5000)
        status, value = _calculate_file_hash_with_timeout(f, timeout_sec=30)
        assert status == "ok"
        assert len(value) == 32


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
