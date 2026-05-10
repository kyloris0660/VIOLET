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
    CONFIRM_STEPS,
    EXECUTE_STEPS,
    FORBIDDEN_DB_NAMES,
    SAFE_STEPS,
    count_dir_files,
    count_supported_files,
    ensure_env_before_execute,
    filter_confirmed_steps,
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


# ---------------------------------------------------------------------------
# ensure_env_before_execute  (P1 #1 — forced env validation)
# ---------------------------------------------------------------------------

class TestEnsureEnvBeforeExecute:

    def test_import_without_env_prepends_env(self):
        result = ensure_env_before_execute(["import"])
        assert result[0] == "env"
        assert "import" in result

    def test_execute_step_import_requires_env_first(self):
        """``--step import --execute`` must have env before import."""
        result = ensure_env_before_execute(["import"])
        assert result == ["env", "import"]

    def test_clip_without_env_prepends_env(self):
        result = ensure_env_before_execute(["clip"])
        assert result[0] == "env"

    def test_translate_without_env_prepends_env(self):
        result = ensure_env_before_execute(["translate"])
        assert result[0] == "env"

    def test_aitag_without_env_prepends_env(self):
        result = ensure_env_before_execute(["ai-tag"])
        assert result[0] == "env"

    def test_env_already_present_no_duplication(self):
        result = ensure_env_before_execute(["env", "import"])
        assert result.count("env") == 1
        assert result == ["env", "import"]

    def test_safe_steps_only_no_env_prepend(self):
        """Safe steps (fixture, browser, etc.) should NOT force env."""
        result = ensure_env_before_execute(["fixture", "browser"])
        assert "env" not in result

    def test_empty_list(self):
        result = ensure_env_before_execute([])
        assert result == []

    def test_all_execute_steps_without_env(self):
        result = ensure_env_before_execute(["import", "clip", "ai-tag", "translate"])
        assert result[0] == "env"
        assert len(result) == 5

    def test_mixed_safe_and_execute_prepends_env(self):
        result = ensure_env_before_execute(["fixture", "import"])
        assert result[0] == "env"
        assert result == ["env", "fixture", "import"]

    def test_returns_new_list(self):
        """Must return a copy, not mutate the original."""
        original = ["import"]
        result = ensure_env_before_execute(original)
        assert result is not original
        assert original == ["import"]  # unchanged


# ---------------------------------------------------------------------------
# filter_confirmed_steps  (P1 #2 — no mutation during iteration)
# ---------------------------------------------------------------------------

class TestFilterConfirmedSteps:

    def test_declining_aitag_does_not_skip_translate_confirmation(self):
        """Declining ai-tag must NOT skip translate — it must still appear."""
        steps = ["env", "import", "ai-tag", "translate"]
        result = filter_confirmed_steps(
            steps, execute=True, yes=False, declined=frozenset({"ai-tag"})
        )
        assert "ai-tag" not in result
        assert "translate" in result
        assert result == ["env", "import", "translate"]

    def test_declining_translate_removes_translate(self):
        steps = ["env", "import", "ai-tag", "translate"]
        result = filter_confirmed_steps(
            steps, execute=True, yes=False, declined=frozenset({"translate"})
        )
        assert "translate" not in result
        assert "ai-tag" in result

    def test_declining_both_removes_both(self):
        steps = ["env", "import", "ai-tag", "translate"]
        result = filter_confirmed_steps(
            steps, execute=True, yes=False, declined=frozenset({"ai-tag", "translate"})
        )
        assert "ai-tag" not in result
        assert "translate" not in result
        assert result == ["env", "import"]

    def test_yes_flag_keeps_all_confirm_steps(self):
        steps = ["env", "import", "ai-tag", "translate"]
        result = filter_confirmed_steps(
            steps, execute=True, yes=True, declined=frozenset({"ai-tag"})
        )
        # --yes overrides declined set
        assert "ai-tag" in result
        assert "translate" in result
        assert result == steps

    def test_non_confirm_steps_unchanged(self):
        steps = ["env", "fixture", "db", "preflight", "import"]
        result = filter_confirmed_steps(
            steps, execute=True, yes=False, declined=frozenset()
        )
        assert result == steps

    def test_no_execute_keeps_all(self):
        steps = ["ai-tag", "translate"]
        result = filter_confirmed_steps(
            steps, execute=False, yes=False, declined=frozenset({"ai-tag"})
        )
        # Without --execute, confirm logic doesn't apply
        assert result == steps

    def test_declined_none_keeps_all(self):
        """When declined is None (pre-interactive), keep everything."""
        steps = ["env", "ai-tag", "translate"]
        result = filter_confirmed_steps(
            steps, execute=True, yes=False, declined=None
        )
        assert result == steps

    def test_returns_new_list(self):
        """Must return a copy, not the original list."""
        original = ["env", "import", "ai-tag"]
        result = filter_confirmed_steps(
            original, execute=True, yes=True
        )
        assert result is not original
        assert result == original  # same content, different object
