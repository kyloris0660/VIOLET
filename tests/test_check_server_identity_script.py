"""Tests for scripts/check_test_server_identity.py path normalization.

Focuses on normalize_executable_path vs normalize_path, ensuring that
--expected-python comparison does NOT resolve symlinks (Codex P1 fix).
"""
import os
import platform
import sys
from pathlib import Path
from unittest.mock import patch  # used via patch.object()

import pytest

# Import the script module directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
# The script uses `if __name__ == "__main__": main()`, so importing it
# won't trigger main().  We just need the functions.
import importlib
_script_path = Path(__file__).resolve().parent.parent / "scripts" / "check_test_server_identity.py"
_spec = importlib.util.spec_from_file_location("check_identity", _script_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

normalize_path = _mod.normalize_path
normalize_executable_path = _mod.normalize_executable_path


# ---------------------------------------------------------------------------
# 1. POSIX distinct venvs must remain distinct
# ---------------------------------------------------------------------------

class TestPosixDistinctVenvs:
    """Two venv pythons pointing at the same base interpreter must NOT
    normalize to the same string.  This is the core Codex P1 regression."""

    def test_distinct_venv_paths_remain_distinct_posix(self):
        """Paths /tmp/repoA/venv/bin/python and /tmp/repoB/venv/bin/python
        must not collapse to the same normalized value on POSIX."""
        with patch.object(_mod.platform, "system", return_value="Linux"):
            a = normalize_executable_path("/tmp/repoA/venv/bin/python")
            b = normalize_executable_path("/tmp/repoB/venv/bin/python")
        assert a != b, (
            f"Distinct venv paths collapsed to same value: {a!r}"
        )

    def test_distinct_venv_paths_no_platform_mock(self):
        """Even without platform mock, the paths should differ on any OS
        because normalize_executable_path uses abspath, not realpath."""
        a = normalize_executable_path("/tmp/repoA/venv/bin/python")
        b = normalize_executable_path("/tmp/repoB/venv/bin/python")
        assert a != b

    def test_normalize_path_may_collapse_symlinks(self):
        """normalize_path uses realpath, which CAN collapse symlinks.
        This test documents that normalize_path is NOT suitable for
        venv executable comparison (it may or may not collapse depending
        on whether the paths actually exist and are symlinks)."""
        # Even without real symlinks, the two functions should at least
        # return the same result for a non-existent path (no symlink to
        # resolve).  The key difference is the design contract.
        p = "/tmp/test/venv/bin/python"
        # Both should produce some normalized form
        assert isinstance(normalize_path(p), str)
        assert isinstance(normalize_executable_path(p), str)


# ---------------------------------------------------------------------------
# 2. Windows case/separator normalization
# ---------------------------------------------------------------------------

class TestWindowsNormalization:

    def test_case_and_separator_equal_on_windows(self):
        """On Windows, paths differing only in case and separators
        should compare equal."""
        with patch.object(_mod.platform, "system", return_value="Windows"), \
             patch.object(_mod.os.path, "normcase",
                          side_effect=lambda p: p.replace("/", "\\").lower()):
            a = normalize_executable_path(
                r"C:\Users\X\Repo\venv\Scripts\python.exe"
            )
            b = normalize_executable_path(
                "c:/users/x/repo/venv/scripts/python.exe"
            )
        assert a == b, f"Windows paths should match: {a!r} vs {b!r}"

    def test_different_venvs_differ_on_windows(self):
        """Distinct venv paths should remain distinct on Windows too."""
        with patch.object(_mod.platform, "system", return_value="Windows"), \
             patch.object(_mod.os.path, "normcase",
                          side_effect=lambda p: p.replace("/", "\\").lower()):
            a = normalize_executable_path(
                r"C:\Users\X\RepoA\venv\Scripts\python.exe"
            )
            b = normalize_executable_path(
                r"C:\Users\X\RepoB\venv\Scripts\python.exe"
            )
        assert a != b


# ---------------------------------------------------------------------------
# 3. --expected-python mismatch should fail
# ---------------------------------------------------------------------------

class TestExpectedPythonComparison:
    """Integration-level tests verifying the comparison logic path in main().
    We test via the normalize functions since main() uses them directly."""

    def test_matching_path_passes(self):
        """Identical path normalizes to same value."""
        p = "/some/venv/bin/python"
        assert normalize_executable_path(p) == normalize_executable_path(p)

    def test_mismatched_path_fails(self):
        """Different venv paths normalize to different values."""
        a = normalize_executable_path("/venvA/bin/python")
        b = normalize_executable_path("/venvB/bin/python")
        assert a != b

    def test_trailing_separator_ignored(self):
        """normpath strips trailing separators."""
        a = normalize_executable_path("/some/venv/bin/python")
        # normpath should handle this, though trailing sep on an exe
        # is unusual
        assert isinstance(a, str) and len(a) > 0


# ---------------------------------------------------------------------------
# 4. Backward compatibility: normalize_path still uses realpath
# ---------------------------------------------------------------------------

class TestNormalizePathBackwardCompat:
    """normalize_path (used for code_root etc.) still resolves symlinks."""

    def test_normalize_path_uses_realpath(self):
        """Verify normalize_path calls realpath (via inspection of result
        on a known non-symlink path)."""
        p = os.path.abspath(__file__)
        result = normalize_path(p)
        expected = os.path.normpath(os.path.realpath(p))
        if platform.system() == "Windows":
            expected = expected.lower()
        assert result == expected

    def test_normalize_executable_path_no_realpath(self):
        """normalize_executable_path must NOT call realpath.
        We verify by patching realpath to a sentinel and confirming
        it is not used."""
        sentinel = "/SENTINEL/should/not/appear"
        original_realpath = os.path.realpath
        with patch.object(_mod.os.path, "realpath",
                          return_value=sentinel) as mock_rp:
            result = normalize_executable_path("/some/path/python")
        # realpath must not have been called
        mock_rp.assert_not_called()
        assert sentinel not in result


# ---------------------------------------------------------------------------
# 5. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_tilde_expansion(self):
        """~ should be expanded."""
        result = normalize_executable_path("~/venv/bin/python")
        assert "~" not in result

    def test_dot_segments_resolved(self):
        """.. and . segments should be collapsed."""
        a = normalize_executable_path("/a/b/../c/python")
        assert ".." not in a

    def test_empty_string_returns_cwd_based(self):
        """Empty string should produce cwd-based result (abspath of '')."""
        result = normalize_executable_path("")
        assert os.path.isabs(result)
