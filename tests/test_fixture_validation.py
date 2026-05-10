"""Fixture validation tests — read-only verification of VioletTestFixture.

These tests confirm the fixture directory structure and file counts.
They never modify, move, or delete any files.

Requires: VIOLET_TEST_FIXTURE_PATH env variable pointing to VioletTestFixture.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))


class TestFixtureStructure:

    def test_fixture_path_exists(self, fixture_path):
        assert fixture_path.is_dir()

    def test_expected_subfolders_exist(self, fixture_path):
        for name in ("anime", "non_anime", "mixed"):
            sub = fixture_path / name
            assert sub.is_dir(), f"Expected subfolder missing: {sub}"

    def test_anime_has_supported_images(self, fixture_counts):
        assert fixture_counts["anime"] > 0, "anime/ should contain supported images"

    def test_non_anime_has_supported_images(self, fixture_counts):
        assert fixture_counts["non_anime"] > 0, "non_anime/ should contain supported images"

    def test_mixed_has_supported_images(self, fixture_counts):
        assert fixture_counts["mixed"] > 0, "mixed/ should contain supported images"

    def test_total_reasonable_range(self, fixture_counts):
        total = fixture_counts["total"]
        assert 10 <= total <= 500, f"Expected 10-500 total images, got {total}"

    def test_fixture_files_are_readonly_safe(self, fixture_path):
        """Confirm we can stat files without opening them."""
        count = 0
        for subfolder in ("anime", "non_anime", "mixed"):
            sub = fixture_path / subfolder
            if sub.is_dir():
                for f in sub.iterdir():
                    assert f.exists()
                    _ = f.stat()
                    count += 1
        assert count > 0


class TestInspectFixtureScript:

    def test_inspect_script_exists(self):
        script = Path(__file__).resolve().parent.parent / "scripts" / "inspect_test_fixture.py"
        assert script.is_file(), f"inspect_test_fixture.py not found at {script}"

    def test_inspect_returns_valid_result(self, fixture_path):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from inspect_test_fixture import inspect_fixture
        result = inspect_fixture(fixture_path)
        assert result["exists"] is True
        assert result["total_supported"] > 0
        assert "anime" in result["subfolders"]
        assert "non_anime" in result["subfolders"]
        assert "mixed" in result["subfolders"]

    def test_inspect_missing_dir_reports_error(self, tmp_path):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from inspect_test_fixture import inspect_fixture
        result = inspect_fixture(tmp_path / "nonexistent")
        assert result["exists"] is False
        assert len(result["errors"]) > 0
