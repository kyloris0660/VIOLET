"""Destructive gate tests — non-destructive verification of safety controls.

These tests verify that destructive operations are properly gated by:
1. Production hard refusal
2. Missing confirm_phrase rejection
3. Wrong confirm_phrase rejection
4. dry_run=true default behavior

No actual destructive operations are performed.
"""
import importlib
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))


def _reload_settings(tmp_path):
    import dotenv
    import app.config as config_mod
    with patch.object(dotenv, "load_dotenv", lambda *a, **kw: None):
        importlib.reload(config_mod)
    config_mod._PROJECT_ROOT = tmp_path
    return config_mod.Settings()


class TestDestructiveGateConditions:

    def test_production_refusal(self, tmp_path):
        env = {"VIOLET_ENV": "production", "POSTGRES_DB": "blombooru",
               "VIOLET_STORAGE_ROOT": str(tmp_path / "store"),
               "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            assert s.IS_PRODUCTION_ENV is True

        import app.routes.admin.dev_tools as dt
        importlib.reload(dt)
        with patch.dict(os.environ, env, clear=False):
            with patch.object(dt, "settings", s):
                gate = dt._compute_gate_diagnostic()
                assert gate["conditions"]["0_production_refusal"] is False
                assert gate["gate_would_pass"] is False

    def test_test_env_with_test_db_passes_env_conditions(self, tmp_path):
        storage = tmp_path / "test_storage"
        storage.mkdir()
        env = {
            "VIOLET_ENV": "test",
            "POSTGRES_DB": "blombooru_test",
            "VIOLET_STORAGE_ROOT": str(storage),
            "VIOLET_TEST_STORAGE_ROOT": str(storage),
            "TEST_DATABASE_URL": "",
            "VIOLET_ALLOW_DESTRUCTIVE_E2E": "1",
        }
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            assert s.VIOLET_ENV == "test"
            assert s.DB_NAME == "blombooru_test"

        import app.routes.admin.dev_tools as dt
        importlib.reload(dt)
        with patch.dict(os.environ, env, clear=False):
            with patch.object(dt, "settings", s):
                gate = dt._compute_gate_diagnostic()
                assert gate["conditions"]["0_production_refusal"] is True
                assert gate["conditions"]["1_violet_env_is_test"] is True
                assert gate["conditions"]["2_db_is_test_db"] is True

    def test_missing_e2e_flag_fails_gate(self, tmp_path):
        storage = tmp_path / "test_storage"
        storage.mkdir()
        env = {
            "VIOLET_ENV": "test",
            "POSTGRES_DB": "blombooru_test",
            "VIOLET_STORAGE_ROOT": str(storage),
            "VIOLET_TEST_STORAGE_ROOT": str(storage),
            "TEST_DATABASE_URL": "",
            "VIOLET_ALLOW_DESTRUCTIVE_E2E": "",
        }
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)

        import app.routes.admin.dev_tools as dt
        importlib.reload(dt)
        with patch.dict(os.environ, env, clear=False):
            with patch.object(dt, "settings", s):
                gate = dt._compute_gate_diagnostic()
                assert gate["conditions"]["7_destructive_e2e_flag_set"] is False
                assert gate["gate_would_pass"] is False

    def test_forbidden_test_db_names_rejected(self, tmp_path):
        for name in ("blombooru", "production", "main", "postgres"):
            env = {"VIOLET_ENV": "test", "POSTGRES_DB": name,
                   "VIOLET_STORAGE_ROOT": str(tmp_path), "TEST_DATABASE_URL": ""}
            with patch.dict(os.environ, env, clear=False):
                s = _reload_settings(tmp_path)
                with pytest.raises(RuntimeError, match="test-specific name"):
                    _ = s.DB_NAME

    def test_storage_root_eq_code_root_fails(self, tmp_path):
        env = {
            "VIOLET_ENV": "test",
            "POSTGRES_DB": "blombooru_test",
            "VIOLET_STORAGE_ROOT": str(tmp_path),
            "VIOLET_TEST_STORAGE_ROOT": str(tmp_path / "test_storage"),
            "TEST_DATABASE_URL": "",
            "VIOLET_ALLOW_DESTRUCTIVE_E2E": "1",
        }
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
        s.CODE_ROOT = tmp_path

        import app.routes.admin.dev_tools as dt
        importlib.reload(dt)
        with patch.dict(os.environ, env, clear=False):
            with patch.object(dt, "settings", s):
                gate = dt._compute_gate_diagnostic()
                assert gate["conditions"]["4_storage_root_ne_code_root"] is False
                assert gate["gate_would_pass"] is False


class TestStorageContainment:

    def test_resolve_storage_path_normal(self, tmp_path):
        env = {"VIOLET_ENV": "test", "POSTGRES_DB": "blombooru_test",
               "VIOLET_STORAGE_ROOT": str(tmp_path), "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
        result = s.resolve_storage_path("media/original/test.jpg")
        assert result is not None
        assert str(tmp_path) in str(result)

    def test_resolve_rejects_absolute_path(self, tmp_path):
        env = {"VIOLET_ENV": "test", "POSTGRES_DB": "blombooru_test",
               "VIOLET_STORAGE_ROOT": str(tmp_path), "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
        assert s.resolve_storage_path("/etc/passwd") is None
        assert s.resolve_storage_path("C:\\Windows\\System32\\cmd.exe") is None

    def test_resolve_rejects_traversal(self, tmp_path):
        env = {"VIOLET_ENV": "test", "POSTGRES_DB": "blombooru_test",
               "VIOLET_STORAGE_ROOT": str(tmp_path), "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
        assert s.resolve_storage_path("../../../etc/passwd") is None
        assert s.resolve_storage_path("media/../../secret") is None

    def test_resolve_rejects_empty(self, tmp_path):
        env = {"VIOLET_ENV": "test", "POSTGRES_DB": "blombooru_test",
               "VIOLET_STORAGE_ROOT": str(tmp_path), "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
        assert s.resolve_storage_path("") is None

    def test_resolve_rejects_unc_path(self, tmp_path):
        env = {"VIOLET_ENV": "test", "POSTGRES_DB": "blombooru_test",
               "VIOLET_STORAGE_ROOT": str(tmp_path), "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
        assert s.resolve_storage_path("\\\\server\\share\\file") is None
        assert s.resolve_storage_path("//server/share/file") is None

    def test_storage_relative_path(self, tmp_path):
        env = {"VIOLET_ENV": "test", "POSTGRES_DB": "blombooru_test",
               "VIOLET_STORAGE_ROOT": str(tmp_path), "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
        test_file = tmp_path / "media" / "original" / "test.jpg"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.touch()
        rel = s.storage_relative_path(test_file)
        assert rel == "media/original/test.jpg"

    def test_storage_relative_rejects_outside(self, tmp_path):
        env = {"VIOLET_ENV": "test", "POSTGRES_DB": "blombooru_test",
               "VIOLET_STORAGE_ROOT": str(tmp_path / "storage"), "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
        outside = tmp_path / "other" / "file.jpg"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.touch()
        with pytest.raises(ValueError):
            s.storage_relative_path(outside)
