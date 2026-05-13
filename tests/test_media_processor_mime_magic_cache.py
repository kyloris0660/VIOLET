"""Tests for python-magic availability caching in media_processor.

Validates that:
- python-magic import/probe runs at most once per process
- Missing magic does not retry or log on every file
- Fallback chain (PIL -> mimetypes -> octet-stream) still works
- Per-file detector.from_file() failures do not disable the detector
- Detector instances are NOT shared across threads (thread-local)
- Concurrent initialization does not expose half-initialized state
"""

import builtins
import logging
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.utils.media_processor import (
    _get_magic_detector,
    _reset_magic_cache,
    get_mime_type,
)


@pytest.fixture(autouse=True)
def _clean_magic_cache():
    """Reset the module-level magic cache before and after every test."""
    _reset_magic_cache()
    yield
    _reset_magic_cache()


# ---------------------------------------------------------------------------
# 1. ModuleNotFoundError - import magic fails
# ---------------------------------------------------------------------------
class TestMagicImportMissing:
    """When python-magic is not installed (ModuleNotFoundError)."""

    def test_probe_attempted_once(self):
        """Import is attempted once; second call skips immediately."""
        call_count = 0
        _original_import = builtins.__import__

        def _counting_import(name, *args, **kwargs):
            nonlocal call_count
            if name == "magic":
                call_count += 1
                raise ModuleNotFoundError("No module named 'magic'")
            return _original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_counting_import):
            result1 = _get_magic_detector()
            result2 = _get_magic_detector()

        assert result1 is None
        assert result2 is None
        assert call_count == 1, "import magic should be attempted exactly once"

    def test_get_mime_type_no_retry(self, tmp_path):
        """get_mime_type() does not retry magic import on each call."""
        # Create a minimal PNG file
        png = tmp_path / "test.png"
        # Minimal valid PNG: 8-byte signature + IHDR + IEND
        png.write_bytes(
            b"\x89PNG\r\n\x1a\n"  # PNG signature
            b"\x00\x00\x00\rIHDR"  # IHDR chunk
            b"\x00\x00\x00\x01"  # width=1
            b"\x00\x00\x00\x01"  # height=1
            b"\x08\x02"  # bit depth=8, color type=2 (RGB)
            b"\x00\x00\x00"  # compression, filter, interlace
            b"\x90wS\xde"  # CRC (for this exact IHDR)
            b"\x00\x00\x00\x00IEND"  # IEND chunk
            b"\xaeB`\x82"  # CRC for IEND
        )

        call_count = 0
        _original_import = builtins.__import__

        def _counting_import(name, *args, **kwargs):
            nonlocal call_count
            if name == "magic":
                call_count += 1
                raise ModuleNotFoundError("No module named 'magic'")
            return _original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_counting_import):
            mime1 = get_mime_type(png)
            mime2 = get_mime_type(png)

        assert call_count == 1, "import magic should be attempted exactly once across multiple get_mime_type calls"
        # Both calls should return a valid MIME via PIL/mimetypes fallback
        assert "/" in mime1
        assert "/" in mime2

    def test_fallback_returns_valid_mime(self, tmp_path):
        """With magic unavailable, fallback chain still returns a valid MIME."""
        png = tmp_path / "photo.png"
        png.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
            b"\x90wS\xde"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        with patch(
            "builtins.__import__",
            side_effect=_make_magic_blocker(),
        ):
            mime = get_mime_type(png)

        assert mime == "image/png"


# ---------------------------------------------------------------------------
# 2. magic.Magic() construction failure
# ---------------------------------------------------------------------------
class TestMagicConstructionFailure:
    """When `import magic` succeeds but `magic.Magic(mime=True)` raises."""

    def test_construction_failure_cached(self):
        """Magic() failure is cached; not retried on second call."""
        mock_magic_module = MagicMock()
        mock_magic_module.Magic.side_effect = OSError("libmagic not found")

        with patch.dict("sys.modules", {"magic": mock_magic_module}):
            result1 = _get_magic_detector()
            result2 = _get_magic_detector()

        assert result1 is None
        assert result2 is None
        # Magic() constructor called only once (during probe, which creates
        # a test detector to verify libmagic works).
        assert mock_magic_module.Magic.call_count == 1

    def test_fallback_still_works_after_construction_failure(self, tmp_path):
        """Fallback chain works when Magic() construction failed."""
        jpg = tmp_path / "photo.jpg"
        # Minimal JPEG: SOI + EOI
        jpg.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16 + b"\xff\xd9")

        mock_magic_module = MagicMock()
        mock_magic_module.Magic.side_effect = OSError("libmagic not found")

        with patch.dict("sys.modules", {"magic": mock_magic_module}):
            mime = get_mime_type(jpg)

        # Should get a valid MIME from PIL or mimetypes
        assert "/" in mime


# ---------------------------------------------------------------------------
# 3. python-magic available - detector reused within same thread
# ---------------------------------------------------------------------------
class TestMagicAvailableAndCached:
    """When python-magic is installed and working, detector is reused
    within the same thread (thread-local)."""

    def test_detector_created_once_per_thread(self):
        """Within a single thread, the detector is created once and reused."""
        mock_detector = MagicMock()
        mock_magic_module = MagicMock()
        # First call during probe (test detector), second creates thread-local
        mock_magic_module.Magic.side_effect = [MagicMock(), mock_detector]

        with patch.dict("sys.modules", {"magic": mock_magic_module}):
            d1 = _get_magic_detector()
            d2 = _get_magic_detector()
            d3 = _get_magic_detector()

        assert d1 is mock_detector
        assert d2 is mock_detector
        assert d3 is mock_detector
        # Magic() called twice: once during probe (test detector), once for
        # the thread-local detector.
        assert mock_magic_module.Magic.call_count == 2

    def test_detector_used_for_mime(self, tmp_path):
        """get_mime_type() uses the thread-local detector's from_file()."""
        f = tmp_path / "img.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

        mock_detector = MagicMock()
        mock_detector.from_file.return_value = "image/png"

        _reset_magic_cache()
        with patch(
            "app.utils.media_processor._get_magic_detector",
            return_value=mock_detector,
        ):
            mime1 = get_mime_type(f)
            mime2 = get_mime_type(f)

        assert mime1 == "image/png"
        assert mime2 == "image/png"
        # from_file() called twice (once per file)
        assert mock_detector.from_file.call_count == 2


# ---------------------------------------------------------------------------
# 4. Per-file from_file() failure does not disable the detector
# ---------------------------------------------------------------------------
class TestPerFileFailure:
    """detector.from_file() failure on one file does not disable the detector."""

    def test_detector_survives_per_file_error(self, tmp_path):
        """A from_file() exception on file A doesn't prevent use on file B."""
        file_a = tmp_path / "bad.bin"
        file_a.write_bytes(b"\x00" * 10)
        file_b = tmp_path / "good.png"
        file_b.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

        call_log = []
        mock_detector = MagicMock()

        def _from_file(path):
            call_log.append(path)
            if "bad" in path:
                raise OSError("Cannot read file")
            return "image/png"

        mock_detector.from_file.side_effect = _from_file

        _reset_magic_cache()
        with patch(
            "app.utils.media_processor._get_magic_detector",
            return_value=mock_detector,
        ):
            mime_a = get_mime_type(file_a)  # fails -> fallback
            mime_b = get_mime_type(file_b)  # should still use detector

        # file_b should get the magic result
        assert mime_b == "image/png"
        # file_a falls back to something valid
        assert "/" in mime_a
        # Both files had from_file() attempted
        assert len(call_log) == 2


# ---------------------------------------------------------------------------
# 5. Logging behavior - warning emitted once, not per file
# ---------------------------------------------------------------------------
class TestLoggingBehavior:
    """Missing python-magic warning is logged once, not per file."""

    def test_unavailable_warning_logged_once(self, tmp_path, caplog):
        """When magic is missing, the warning appears exactly once."""
        f1 = tmp_path / "a.png"
        f1.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
        f2 = tmp_path / "b.png"
        f2.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

        with patch(
            "builtins.__import__",
            side_effect=_make_magic_blocker(),
        ):
            with caplog.at_level(logging.WARNING):
                get_mime_type(f1)
                get_mime_type(f2)

        unavailable_msgs = [
            r for r in caplog.records
            if "python-magic unavailable" in r.message
        ]
        assert len(unavailable_msgs) == 1, (
            f"Expected 1 'python-magic unavailable' warning, got {len(unavailable_msgs)}"
        )

    def test_per_file_failure_still_logged(self, tmp_path, caplog):
        """Per-file from_file() errors are still logged (they're file-specific)."""
        f = tmp_path / "corrupt.bin"
        f.write_bytes(b"\x00" * 10)

        mock_detector = MagicMock()
        mock_detector.from_file.side_effect = OSError("read error")

        _reset_magic_cache()
        with patch(
            "app.utils.media_processor._get_magic_detector",
            return_value=mock_detector,
        ):
            with caplog.at_level(logging.WARNING):
                get_mime_type(f)

        per_file_msgs = [
            r for r in caplog.records
            if "python-magic failed for" in r.message
        ]
        assert len(per_file_msgs) == 1


# ---------------------------------------------------------------------------
# 6. Thread safety - detector NOT shared across threads
# ---------------------------------------------------------------------------
class TestThreadSafety:
    """Detector instances are per-thread, never shared across threads."""

    def test_each_thread_gets_own_detector(self):
        """Two threads calling _get_magic_detector() receive different
        Magic instances (thread-local isolation)."""
        detectors_by_thread = {}
        mock_magic_module = MagicMock()
        # Each Magic() call returns a distinct mock
        mock_magic_module.Magic.side_effect = lambda mime=True: MagicMock()

        barrier = threading.Barrier(2)

        def worker(tid):
            d = _get_magic_detector()
            detectors_by_thread[tid] = d
            barrier.wait(timeout=5)

        with patch.dict("sys.modules", {"magic": mock_magic_module}):
            t1 = threading.Thread(target=worker, args=(1,))
            t2 = threading.Thread(target=worker, args=(2,))
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

        assert 1 in detectors_by_thread and 2 in detectors_by_thread
        assert detectors_by_thread[1] is not detectors_by_thread[2], (
            "Detectors must be per-thread, not globally shared"
        )

    def test_same_thread_reuses_detector(self):
        """Within one thread, repeated calls return the same instance."""
        mock_magic_module = MagicMock()
        detector_instance = MagicMock()
        # probe creates test detector, then thread-local creates the real one
        mock_magic_module.Magic.side_effect = [MagicMock(), detector_instance]

        with patch.dict("sys.modules", {"magic": mock_magic_module}):
            d1 = _get_magic_detector()
            d2 = _get_magic_detector()

        assert d1 is d2 is detector_instance


# ---------------------------------------------------------------------------
# 7. Concurrent initialization - no half-initialized state
# ---------------------------------------------------------------------------
class TestConcurrentInitialization:
    """Concurrent callers must not observe half-initialized availability state."""

    def test_concurrent_probes_see_consistent_state(self):
        """Multiple threads starting simultaneously all see the same
        final availability result (no thread sees available=True while
        another sees available=False for the same probe)."""
        results = {}
        num_threads = 4
        barrier = threading.Barrier(num_threads)

        mock_magic_module = MagicMock()
        mock_magic_module.Magic.side_effect = lambda mime=True: MagicMock()

        def worker(tid):
            barrier.wait(timeout=5)  # synchronize start
            d = _get_magic_detector()
            results[tid] = d is not None

        with patch.dict("sys.modules", {"magic": mock_magic_module}):
            threads = [
                threading.Thread(target=worker, args=(i,))
                for i in range(num_threads)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

        # All threads must agree on availability
        values = list(results.values())
        assert len(values) == num_threads
        assert all(v == values[0] for v in values), (
            f"Inconsistent availability across threads: {results}"
        )

    def test_slow_probe_blocks_concurrent_callers(self):
        """A slow first probe should block concurrent callers via the lock,
        not let them see _MAGIC_PROBE_DONE=False and start a second probe."""
        import app.utils.media_processor as mp

        probe_count = 0
        original_probe = mp._probe_magic

        def slow_probe():
            nonlocal probe_count
            probe_count += 1
            import time
            time.sleep(0.1)  # simulate slow import
            original_probe()

        barrier = threading.Barrier(2)
        results = {}

        def worker(tid):
            barrier.wait(timeout=5)
            d = _get_magic_detector()
            results[tid] = d

        mock_magic_module = MagicMock()
        mock_magic_module.Magic.side_effect = lambda mime=True: MagicMock()

        with patch.dict("sys.modules", {"magic": mock_magic_module}), \
             patch.object(mp, "_probe_magic", side_effect=slow_probe):
            t1 = threading.Thread(target=worker, args=(1,))
            t2 = threading.Thread(target=worker, args=(2,))
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

        # _probe_magic should have been called at most once despite
        # two threads racing.  (Due to double-checked locking, the second
        # thread waits on the lock and finds _MAGIC_PROBE_DONE=True.)
        assert probe_count <= 1, (
            f"_probe_magic called {probe_count} times; expected at most 1"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_magic_blocker():
    """Return an __import__ side_effect that blocks only ``import magic``."""
    _original_import = builtins.__import__

    def _blocking_import(name, *args, **kwargs):
        if name == "magic":
            raise ModuleNotFoundError("No module named 'magic'")
        return _original_import(name, *args, **kwargs)

    return _blocking_import
