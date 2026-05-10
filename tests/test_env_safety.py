"""Unit tests for Phase 3.1.1a — Environment / DB / Storage Safety Foundation.

Tests cover: VIOLET_ENV, CODE_ROOT / STORAGE_ROOT separation, test DB
fail-closed logic, destructive operation gate, assert_test_db(), and
production hard refusal.
"""
import importlib
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))


def _reload_settings(tmp_path):
    """Reload config module with load_dotenv suppressed so env vars come
    exclusively from patch.dict.  Must be called inside a patch.dict context."""
    import dotenv
    import app.config as config_mod
    with patch.object(dotenv, "load_dotenv", lambda *a, **kw: None):
        importlib.reload(config_mod)
    config_mod._PROJECT_ROOT = tmp_path
    return config_mod.Settings()


# ---------------------------------------------------------------------------
# 1. VIOLET_ENV
# ---------------------------------------------------------------------------

class TestVioletEnv:

    def test_default_is_development(self, tmp_path):
        env = {"VIOLET_ENV": "development", "POSTGRES_DB": "blombooru",
               "VIOLET_STORAGE_ROOT": "", "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            assert s.VIOLET_ENV == "development"

    def test_test_env(self, tmp_path):
        env = {"VIOLET_ENV": "test", "POSTGRES_DB": "blombooru_test",
               "VIOLET_STORAGE_ROOT": "", "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            assert s.VIOLET_ENV == "test"
            assert s.IS_TEST_ENV is True

    def test_production_env(self, tmp_path):
        env = {"VIOLET_ENV": "production", "POSTGRES_DB": "blombooru",
               "VIOLET_STORAGE_ROOT": "", "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            assert s.VIOLET_ENV == "production"
            assert s.IS_PRODUCTION_ENV is True

    def test_invalid_env_raises(self, tmp_path):
        env = {"VIOLET_ENV": "staging", "POSTGRES_DB": "blombooru",
               "VIOLET_STORAGE_ROOT": "", "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            with pytest.raises(RuntimeError, match="Invalid VIOLET_ENV"):
                _ = s.VIOLET_ENV


# ---------------------------------------------------------------------------
# 2. CODE_ROOT / STORAGE_ROOT separation
# ---------------------------------------------------------------------------

class TestStorageRootSeparation:

    def test_storage_root_defaults_to_code_root(self, tmp_path):
        env = {"VIOLET_ENV": "development", "POSTGRES_DB": "blombooru",
               "VIOLET_STORAGE_ROOT": "", "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            assert s.STORAGE_ROOT == s.CODE_ROOT

    def test_storage_root_from_env(self, tmp_path):
        custom = str(tmp_path / "custom_storage")
        env = {"VIOLET_ENV": "development", "POSTGRES_DB": "blombooru",
               "VIOLET_STORAGE_ROOT": custom, "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            assert str(s.STORAGE_ROOT) == custom
            assert s.STORAGE_ROOT_EXPLICITLY_SET is True

    def test_storage_dirs_derive_from_storage_root(self, tmp_path):
        custom = str(tmp_path / "custom_storage")
        env = {"VIOLET_ENV": "development", "POSTGRES_DB": "blombooru",
               "VIOLET_STORAGE_ROOT": custom, "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            assert str(s.MEDIA_DIR).startswith(custom)
            assert str(s.ORIGINAL_DIR).startswith(custom)
            assert str(s.THUMBNAIL_DIR).startswith(custom)
            assert str(s.DATA_DIR).startswith(custom)


# ---------------------------------------------------------------------------
# 3. Test DB fail-closed logic
# ---------------------------------------------------------------------------

class TestDbFailClosed:

    def test_test_env_with_prod_db_name_raises(self, tmp_path):
        env = {"VIOLET_ENV": "test", "POSTGRES_DB": "blombooru",
               "VIOLET_STORAGE_ROOT": "", "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            with pytest.raises(RuntimeError, match="no valid test DB configured"):
                _ = s.DB_NAME

    def test_test_env_with_empty_db_raises(self, tmp_path):
        env = {"VIOLET_ENV": "test", "POSTGRES_DB": "",
               "VIOLET_STORAGE_ROOT": "", "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            with pytest.raises(RuntimeError, match="no valid test DB configured"):
                _ = s.DB_NAME

    def test_test_env_with_valid_test_db(self, tmp_path):
        env = {"VIOLET_ENV": "test", "POSTGRES_DB": "blombooru_test",
               "VIOLET_STORAGE_ROOT": "", "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            assert s.DB_NAME == "blombooru_test"

    def test_test_env_with_test_database_url(self, tmp_path):
        env = {"VIOLET_ENV": "test", "POSTGRES_DB": "",
               "VIOLET_STORAGE_ROOT": "", "TEST_DATABASE_URL": "postgresql://u:p@localhost/my_test_db"}
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            assert s.DB_NAME == "my_test_db"

    def test_dev_env_allows_prod_db_name(self, tmp_path):
        env = {"VIOLET_ENV": "development", "POSTGRES_DB": "blombooru",
               "VIOLET_STORAGE_ROOT": "", "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            assert s.DB_NAME == "blombooru"

    def test_test_database_url_with_prod_name_raises(self, tmp_path):
        env = {"VIOLET_ENV": "test", "POSTGRES_DB": "",
               "VIOLET_STORAGE_ROOT": "", "TEST_DATABASE_URL": "postgresql://u:p@localhost/blombooru"}
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            with pytest.raises(RuntimeError, match="production-like database"):
                _ = s.DB_NAME

    def test_test_database_url_with_valid_name_passes(self, tmp_path):
        env = {"VIOLET_ENV": "test", "POSTGRES_DB": "",
               "VIOLET_STORAGE_ROOT": "", "TEST_DATABASE_URL": "postgresql://u:p@localhost/blombooru_test"}
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            assert s.DB_NAME == "blombooru_test"

    def test_forbidden_db_names_via_postgres_db(self, tmp_path):
        for name in ("production", "main", "postgres"):
            env = {"VIOLET_ENV": "test", "POSTGRES_DB": name,
                   "VIOLET_STORAGE_ROOT": "", "TEST_DATABASE_URL": ""}
            with patch.dict(os.environ, env, clear=False):
                s = _reload_settings(tmp_path)
                with pytest.raises(RuntimeError):
                    _ = s.DB_NAME


# ---------------------------------------------------------------------------
# 4. assert_test_db() — database.py helper
# ---------------------------------------------------------------------------

class TestAssertTestDb:

    def test_rejects_non_test_env(self, tmp_path):
        env = {"VIOLET_ENV": "development", "POSTGRES_DB": "blombooru",
               "VIOLET_STORAGE_ROOT": "", "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            _reload_settings(tmp_path)
            import app.database as db_mod
            importlib.reload(db_mod)
            with pytest.raises(RuntimeError, match="expected 'test'"):
                db_mod.assert_test_db()

    def test_passes_in_test_env(self, tmp_path):
        env = {"VIOLET_ENV": "test", "POSTGRES_DB": "blombooru_test",
               "VIOLET_STORAGE_ROOT": "", "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            _reload_settings(tmp_path)
            import app.database as db_mod
            importlib.reload(db_mod)
            db_mod.assert_test_db()


# ---------------------------------------------------------------------------
# 5. VIOLET_TEST_STORAGE_ROOT config properties
# ---------------------------------------------------------------------------

class TestVioletTestStorageRoot:

    def test_default_is_none(self, tmp_path):
        env = {"VIOLET_ENV": "development", "POSTGRES_DB": "blombooru",
               "VIOLET_STORAGE_ROOT": "", "VIOLET_TEST_STORAGE_ROOT": "",
               "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            assert s.VIOLET_TEST_STORAGE_ROOT is None
            assert s.TEST_STORAGE_ROOT_EXPLICITLY_SET is False

    def test_explicit_value(self, tmp_path):
        tsr = str(tmp_path / "test_storage")
        env = {"VIOLET_ENV": "test", "POSTGRES_DB": "blombooru_test",
               "VIOLET_STORAGE_ROOT": tsr, "VIOLET_TEST_STORAGE_ROOT": tsr,
               "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            assert s.VIOLET_TEST_STORAGE_ROOT == Path(tsr)
            assert s.TEST_STORAGE_ROOT_EXPLICITLY_SET is True


# ---------------------------------------------------------------------------
# 6. Destructive gate — VIOLET_TEST_STORAGE_ROOT conditions
# ---------------------------------------------------------------------------

class TestDestructiveGateStorageConditions:

    def _build_gate_env(self, tmp_path, *, storage_root, test_storage_root="",
                        env="test", db="blombooru_test", e2e_flag="1"):
        return {
            "VIOLET_ENV": env,
            "POSTGRES_DB": db,
            "VIOLET_STORAGE_ROOT": storage_root,
            "VIOLET_TEST_STORAGE_ROOT": test_storage_root,
            "VIOLET_ALLOW_DESTRUCTIVE_E2E": e2e_flag,
            "TEST_DATABASE_URL": "",
        }

    def _get_gate(self, tmp_path, **kwargs):
        env = self._build_gate_env(tmp_path, **kwargs)
        with patch.dict(os.environ, env, clear=False):
            _reload_settings(tmp_path)
            import app.routes.admin.dev_tools as dt_mod
            importlib.reload(dt_mod)
            return dt_mod._compute_gate_diagnostic()

    def test_gate_rejects_when_storage_root_equals_code_root(self, tmp_path):
        tsr = str(tmp_path / "test_storage")
        diag = self._get_gate(tmp_path, storage_root="", test_storage_root=tsr)
        # When VIOLET_STORAGE_ROOT is empty, STORAGE_ROOT defaults to CODE_ROOT
        assert diag["conditions"]["4_storage_root_ne_code_root"] is False

    def test_gate_rejects_when_test_storage_root_missing(self, tmp_path):
        sr = str(tmp_path / "test_storage")
        diag = self._get_gate(tmp_path, storage_root=sr, test_storage_root="")
        assert diag["conditions"]["5_test_storage_root_configured"] is False
        assert diag["conditions"]["6_storage_root_under_test_storage_root"] is False

    def test_gate_rejects_when_storage_root_outside_test_storage_root(self, tmp_path):
        sr = str(tmp_path / "other_storage")
        tsr = str(tmp_path / "test_storage")
        diag = self._get_gate(tmp_path, storage_root=sr, test_storage_root=tsr)
        assert diag["conditions"]["5_test_storage_root_configured"] is True
        assert diag["conditions"]["6_storage_root_under_test_storage_root"] is False

    def test_gate_passes_when_storage_root_under_test_storage_root(self, tmp_path):
        tsr = str(tmp_path / "test_storage")
        sr = str(tmp_path / "test_storage" / "sub")
        diag = self._get_gate(tmp_path, storage_root=sr, test_storage_root=tsr)
        assert diag["conditions"]["5_test_storage_root_configured"] is True
        assert diag["conditions"]["6_storage_root_under_test_storage_root"] is True

    def test_gate_passes_when_storage_root_equals_test_storage_root(self, tmp_path):
        tsr = str(tmp_path / "test_storage")
        diag = self._get_gate(tmp_path, storage_root=tsr, test_storage_root=tsr)
        assert diag["conditions"]["5_test_storage_root_configured"] is True
        assert diag["conditions"]["6_storage_root_under_test_storage_root"] is True
        assert diag["gate_would_pass"] is True

    def test_no_host_specific_paths_required(self, tmp_path):
        tsr = str(tmp_path / "any_path_works")
        diag = self._get_gate(tmp_path, storage_root=tsr, test_storage_root=tsr)
        assert diag["gate_would_pass"] is True
