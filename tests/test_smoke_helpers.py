"""Unit tests for pure helper functions in local_full_pipeline_smoke.py.

These tests do NOT require a running server, database, or any external
resources.  They exercise: count_dir_files, count_supported_files,
validate_base_url, validate_config_diagnostics, mask_sensitive.
"""
import os
import sys
from pathlib import Path

import pytest

# Make the scripts directory importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from local_full_pipeline_smoke import (
    FORBIDDEN_DB_NAMES,
    count_dir_files,
    count_supported_files,
    mask_sensitive,
    validate_base_url,
    validate_config_diagnostics,
)


# ---------------------------------------------------------------------------
# count_dir_files
# ---------------------------------------------------------------------------

class TestCountDirFiles:

    def test_empty_dir(self, tmp_path):
        assert count_dir_files(tmp_path) == 0

    def test_flat_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.png").write_text("b")
        assert count_dir_files(tmp_path) == 2

    def test_nested_files(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "c.jpg").write_text("")
        (tmp_path / "d.gif").write_text("")
        assert count_dir_files(tmp_path) == 2

    def test_ignores_directories(self, tmp_path):
        (tmp_path / "subdir").mkdir()
        (tmp_path / "file.txt").write_text("")
        assert count_dir_files(tmp_path) == 1

    def test_accepts_string_path(self, tmp_path):
        (tmp_path / "x.txt").write_text("")
        assert count_dir_files(str(tmp_path)) == 1


# ---------------------------------------------------------------------------
# count_supported_files
# ---------------------------------------------------------------------------

class TestCountSupportedFiles:

    def test_empty_dir(self, tmp_path):
        assert count_supported_files(tmp_path) == 0

    def test_only_supported(self, tmp_path):
        for ext in (".jpg", ".png", ".gif", ".webp", ".mp4"):
            (tmp_path / f"file{ext}").write_text("")
        assert count_supported_files(tmp_path) == 5

    def test_only_unsupported(self, tmp_path):
        for ext in (".txt", ".pdf", ".psd", ".ai", ".doc"):
            (tmp_path / f"file{ext}").write_text("")
        assert count_supported_files(tmp_path) == 0

    def test_mixed(self, tmp_path):
        (tmp_path / "photo.jpeg").write_text("")
        (tmp_path / "anim.gif").write_text("")
        (tmp_path / "readme.txt").write_text("")
        (tmp_path / "data.csv").write_text("")
        assert count_supported_files(tmp_path) == 2

    def test_case_insensitive(self, tmp_path):
        (tmp_path / "PHOTO.JPG").write_text("")
        (tmp_path / "Video.MP4").write_text("")
        assert count_supported_files(tmp_path) == 2

    def test_nested(self, tmp_path):
        sub = tmp_path / "nested"
        sub.mkdir()
        (sub / "deep.png").write_text("")
        (tmp_path / "top.bmp").write_text("")
        assert count_supported_files(tmp_path) == 2

    def test_avif_and_webm_and_mov(self, tmp_path):
        for ext in (".avif", ".webm", ".mov"):
            (tmp_path / f"file{ext}").write_text("")
        assert count_supported_files(tmp_path) == 3


# ---------------------------------------------------------------------------
# validate_base_url
# ---------------------------------------------------------------------------

class TestValidateBaseUrl:

    def test_localhost_ok(self):
        ok, _ = validate_base_url("http://localhost:8001")
        assert ok is True

    def test_127_ok(self):
        ok, _ = validate_base_url("http://127.0.0.1:8001")
        assert ok is True

    def test_localhost_no_port(self):
        ok, _ = validate_base_url("http://localhost")
        assert ok is True

    def test_remote_host_rejected(self):
        ok, reason = validate_base_url("http://example.com:8001")
        assert ok is False
        assert "example.com" in reason

    def test_ip_rejected(self):
        ok, _ = validate_base_url("http://192.168.1.1:8001")
        assert ok is False

    def test_empty_string_rejected(self):
        ok, _ = validate_base_url("")
        assert ok is False

    def test_https_localhost_ok(self):
        ok, _ = validate_base_url("https://localhost:8001")
        assert ok is True


# ---------------------------------------------------------------------------
# validate_config_diagnostics
# ---------------------------------------------------------------------------

class TestValidateConfigDiagnostics:

    def _make_diag(self, **overrides):
        """Build a valid diagnostics dict, then apply overrides."""
        d = {
            "environment": {"VIOLET_ENV": "test"},
            "database": {"DB_NAME": "blombooru_test"},
            "storage": {
                "STORAGE_ROOT": r"C:\Users\kyloris\VioletStorage\test",
                "CODE_ROOT": r"C:\Users\kyloris\Documents\AnimeLocalBooru",
            },
        }
        for key, val in overrides.items():
            section, field = key.split(".", 1)
            d[section][field] = val
        return d

    def test_valid(self):
        ok, issues = validate_config_diagnostics(self._make_diag())
        assert ok is True
        assert issues == []

    def test_env_not_test(self):
        ok, issues = validate_config_diagnostics(
            self._make_diag(**{"environment.VIOLET_ENV": "development"})
        )
        assert ok is False
        assert any("VIOLET_ENV" in i for i in issues)

    def test_forbidden_db_blombooru(self):
        ok, issues = validate_config_diagnostics(
            self._make_diag(**{"database.DB_NAME": "blombooru"})
        )
        assert ok is False
        assert any("forbidden" in i.lower() for i in issues)

    def test_forbidden_db_production(self):
        ok, issues = validate_config_diagnostics(
            self._make_diag(**{"database.DB_NAME": "production"})
        )
        assert ok is False

    def test_forbidden_db_postgres(self):
        ok, issues = validate_config_diagnostics(
            self._make_diag(**{"database.DB_NAME": "postgres"})
        )
        assert ok is False

    def test_forbidden_db_main(self):
        ok, issues = validate_config_diagnostics(
            self._make_diag(**{"database.DB_NAME": "main"})
        )
        assert ok is False

    def test_empty_db_name(self):
        ok, issues = validate_config_diagnostics(
            self._make_diag(**{"database.DB_NAME": ""})
        )
        assert ok is False
        assert any("empty" in i.lower() for i in issues)

    def test_storage_root_without_test(self):
        ok, issues = validate_config_diagnostics(
            self._make_diag(**{"storage.STORAGE_ROOT": r"C:\Users\kyloris\VioletStorage\prod"})
        )
        assert ok is False
        assert any("test" in i.lower() for i in issues)

    def test_storage_root_case_insensitive(self):
        ok, issues = validate_config_diagnostics(
            self._make_diag(**{"storage.STORAGE_ROOT": r"C:\Users\kyloris\VioletStorage\TEST"})
        )
        assert ok is True

    def test_storage_root_same_as_code_root(self):
        ok, issues = validate_config_diagnostics(
            self._make_diag(
                **{
                    "storage.STORAGE_ROOT": r"C:\Users\kyloris\Documents\AnimeLocalBooru",
                    "storage.CODE_ROOT": r"C:\Users\kyloris\Documents\AnimeLocalBooru",
                }
            )
        )
        assert ok is False
        assert any("CODE_ROOT" in i for i in issues)

    def test_storage_root_is_drive_root(self):
        ok, issues = validate_config_diagnostics(
            self._make_diag(**{"storage.STORAGE_ROOT": "C:\\"})
        )
        assert ok is False
        assert any("filesystem root" in i.lower() for i in issues)

    def test_multiple_issues_reported(self):
        diag = {
            "environment": {"VIOLET_ENV": "development"},
            "database": {"DB_NAME": "blombooru"},
            "storage": {"STORAGE_ROOT": "C:\\", "CODE_ROOT": "C:\\"},
        }
        ok, issues = validate_config_diagnostics(diag)
        assert ok is False
        assert len(issues) >= 3  # env + db + storage issues


# ---------------------------------------------------------------------------
# mask_sensitive
# ---------------------------------------------------------------------------

class TestMaskSensitive:

    def test_long_string(self):
        result = mask_sensitive("abcdefgh1234")
        assert result.endswith("1234")
        assert result.startswith("*")
        assert "abcdefgh" not in result

    def test_short_string(self):
        assert mask_sensitive("abc") == "****"

    def test_exact_visible_chars(self):
        assert mask_sensitive("abcd") == "****"

    def test_visible_chars_param(self):
        result = mask_sensitive("secret_key_value", visible_chars=6)
        assert result.endswith("_value")
        assert result.count("*") == len("secret_key_value") - 6

    def test_empty_string(self):
        assert mask_sensitive("") == "****"

    def test_single_char(self):
        assert mask_sensitive("x") == "****"


# ---------------------------------------------------------------------------
# Safety constants (sanity checks)
# ---------------------------------------------------------------------------

class TestSafetyConstants:

    def test_forbidden_db_names_includes_blombooru(self):
        assert "blombooru" in FORBIDDEN_DB_NAMES

    def test_forbidden_db_names_includes_production(self):
        assert "production" in FORBIDDEN_DB_NAMES

    def test_forbidden_db_names_includes_postgres(self):
        assert "postgres" in FORBIDDEN_DB_NAMES

    def test_forbidden_db_names_includes_main(self):
        assert "main" in FORBIDDEN_DB_NAMES
