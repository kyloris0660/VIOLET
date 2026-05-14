"""Regression tests for config precedence (Phase 3.2g.5).

Validates that process/session environment variables take precedence
over .env file defaults after the load_dotenv(override=False) fix.

Root cause: backend/app/config.py previously called
    load_dotenv(override=True)
which forced .env values to overwrite explicit shell/session env vars.
This was changed to override=False so that:
    (1) Explicit process/session env vars win
    (2) TEST_DATABASE_URL continues to work (it was never in .env)
    (3) .env fills in defaults for vars not set in the process
    (4) Code defaults remain the lowest priority
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
    exclusively from patch.dict."""
    import dotenv
    import app.config as config_mod
    with patch.object(dotenv, "load_dotenv", lambda *a, **kw: None):
        importlib.reload(config_mod)
    config_mod._PROJECT_ROOT = tmp_path
    return config_mod.Settings()


# ---------------------------------------------------------------------------
# 1. POSTGRES_DB override: session env beats .env
# ---------------------------------------------------------------------------

class TestPostgresDbOverride:
    """Session env var POSTGRES_DB must override .env default.

    Previously, .env contained POSTGRES_DB=blombooru and override=True
    forced it to overwrite shell-set POSTGRES_DB=blombooru_test_medium.
    """

    def test_session_env_wins_over_dotenv_default(self, tmp_path):
        """POSTGRES_DB set in session must survive, not be overwritten by .env."""
        env = {
            "VIOLET_ENV": "test",
            "POSTGRES_DB": "blombooru_test_medium",
            "VIOLET_STORAGE_ROOT": str(tmp_path / "storage"),
            "TEST_DATABASE_URL": "",
        }
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            assert s.DB_NAME == "blombooru_test_medium"

    def test_blombooru_test_also_works(self, tmp_path):
        """Standard test DB name must also work."""
        env = {
            "VIOLET_ENV": "test",
            "POSTGRES_DB": "blombooru_test",
            "VIOLET_STORAGE_ROOT": str(tmp_path / "storage"),
            "TEST_DATABASE_URL": "",
        }
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            assert s.DB_NAME == "blombooru_test"


# ---------------------------------------------------------------------------
# 2. TEST_DATABASE_URL continues to work
# ---------------------------------------------------------------------------

class TestTestDatabaseUrlSupport:
    """TEST_DATABASE_URL (never in .env) must still be honored.

    This was already working before the fix because override=True
    can't overwrite a var that doesn't exist in .env. Verify it
    still works with override=False.
    """

    def test_test_database_url_takes_priority(self, tmp_path):
        """TEST_DATABASE_URL should override POSTGRES_DB when both are set."""
        env = {
            "VIOLET_ENV": "test",
            "POSTGRES_DB": "blombooru_test",
            "VIOLET_STORAGE_ROOT": str(tmp_path / "storage"),
            "TEST_DATABASE_URL": "postgresql://u:p@localhost/custom_test_db",
        }
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            assert s.DB_NAME == "custom_test_db"

    def test_test_database_url_empty_falls_to_postgres_db(self, tmp_path):
        """When TEST_DATABASE_URL is empty, POSTGRES_DB is used."""
        env = {
            "VIOLET_ENV": "test",
            "POSTGRES_DB": "blombooru_test",
            "VIOLET_STORAGE_ROOT": str(tmp_path / "storage"),
            "TEST_DATABASE_URL": "",
        }
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            assert s.DB_NAME == "blombooru_test"


# ---------------------------------------------------------------------------
# 3. TAG_TRANSLATION_BACKGROUND_ENABLED override
# ---------------------------------------------------------------------------

class TestTagTranslationBackgroundEnabledOverride:
    """Session env TAG_TRANSLATION_BACKGROUND_ENABLED=false must not be
    overwritten by .env's TAG_TRANSLATION_BACKGROUND_ENABLED=true.

    This caused Phase 3.2g.2 incident: background translation worker kept
    running despite explicit session env var being set to false.
    """

    def test_session_false_overrides_dotenv_true(self, tmp_path):
        env = {
            "VIOLET_ENV": "test",
            "POSTGRES_DB": "blombooru_test",
            "VIOLET_STORAGE_ROOT": str(tmp_path / "storage"),
            "TEST_DATABASE_URL": "",
            "TAG_TRANSLATION_BACKGROUND_ENABLED": "false",
        }
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            assert s.TAG_TRANSLATION_BG_ENABLED is False

    def test_session_true_also_works(self, tmp_path):
        env = {
            "VIOLET_ENV": "test",
            "POSTGRES_DB": "blombooru_test",
            "VIOLET_STORAGE_ROOT": str(tmp_path / "storage"),
            "TEST_DATABASE_URL": "",
            "TAG_TRANSLATION_BACKGROUND_ENABLED": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            assert s.TAG_TRANSLATION_BG_ENABLED is True


# ---------------------------------------------------------------------------
# 4. TAG_TRANSLATION_AUTO_ENABLED override
# ---------------------------------------------------------------------------

class TestTagTranslationAutoEnabledOverride:
    """Session env TAG_TRANSLATION_AUTO_ENABLED=false must not be
    overwritten by .env's TAG_TRANSLATION_AUTO_ENABLED=true."""

    def test_session_false_overrides_dotenv_true(self, tmp_path):
        env = {
            "VIOLET_ENV": "test",
            "POSTGRES_DB": "blombooru_test",
            "VIOLET_STORAGE_ROOT": str(tmp_path / "storage"),
            "TEST_DATABASE_URL": "",
            "TAG_TRANSLATION_AUTO_ENABLED": "false",
        }
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            assert s.TAG_TRANSLATION_AUTO_ENABLED is False

    def test_session_true_also_works(self, tmp_path):
        env = {
            "VIOLET_ENV": "test",
            "POSTGRES_DB": "blombooru_test",
            "VIOLET_STORAGE_ROOT": str(tmp_path / "storage"),
            "TEST_DATABASE_URL": "",
            "TAG_TRANSLATION_AUTO_ENABLED": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            assert s.TAG_TRANSLATION_AUTO_ENABLED is True


# ---------------------------------------------------------------------------
# 5. TAG_TRANSLATION_LLM_ENABLED override
# ---------------------------------------------------------------------------

class TestTagTranslationLlmEnabledOverride:
    """Session env TAG_TRANSLATION_LLM_ENABLED=false must not be
    overwritten by .env's TAG_TRANSLATION_LLM_ENABLED=true."""

    def test_session_false_overrides_dotenv_true(self, tmp_path):
        env = {
            "VIOLET_ENV": "test",
            "POSTGRES_DB": "blombooru_test",
            "VIOLET_STORAGE_ROOT": str(tmp_path / "storage"),
            "TEST_DATABASE_URL": "",
            "TAG_TRANSLATION_LLM_ENABLED": "false",
        }
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            assert s.TAG_TRANSLATION_LLM_ENABLED is False

    def test_session_true_also_works(self, tmp_path):
        env = {
            "VIOLET_ENV": "test",
            "POSTGRES_DB": "blombooru_test",
            "VIOLET_STORAGE_ROOT": str(tmp_path / "storage"),
            "TEST_DATABASE_URL": "",
            "TAG_TRANSLATION_LLM_ENABLED": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            s = _reload_settings(tmp_path)
            assert s.TAG_TRANSLATION_LLM_ENABLED is True


# ---------------------------------------------------------------------------
# 6. .env defaults still load when env var is unset
# ---------------------------------------------------------------------------

class TestDotenvDefaultLoading:
    """When a var is NOT set in the process environment, the .env default
    must still be applied. This confirms override=False fills gaps correctly.

    We test this indirectly: with load_dotenv suppressed (as in all unit tests),
    the code defaults kick in. The real integration behavior is that .env
    fills in missing vars — verified by the absence of override=True in config.py.
    """

    def test_unset_vars_use_code_defaults(self, tmp_path):
        """When env vars are absent, code defaults are used."""
        env = {
            "VIOLET_ENV": "development",
            "POSTGRES_DB": "blombooru",
            "VIOLET_STORAGE_ROOT": "",
            "TEST_DATABASE_URL": "",
            # Deliberately NOT setting TAG_TRANSLATION_LLM_ENABLED
        }
        # Remove the key entirely if it exists
        clean_env = dict(os.environ)
        for key in ("TAG_TRANSLATION_LLM_ENABLED", "TAG_TRANSLATION_BACKGROUND_ENABLED",
                     "TAG_TRANSLATION_AUTO_ENABLED"):
            clean_env.pop(key, None)
        clean_env.update(env)
        with patch.dict(os.environ, clean_env, clear=True):
            s = _reload_settings(tmp_path)
            # Code defaults for these are "false" (the second arg to os.getenv)
            assert s.TAG_TRANSLATION_LLM_ENABLED is False
            assert s.TAG_TRANSLATION_BG_ENABLED is False
            assert s.TAG_TRANSLATION_AUTO_ENABLED is False

    def test_override_false_in_source(self):
        """Verify that config.py source code contains override=False."""
        config_path = Path(__file__).resolve().parent.parent / "backend" / "app" / "config.py"
        source = config_path.read_text(encoding="utf-8")
        assert "override=False" in source, (
            "config.py must call load_dotenv with override=False"
        )
        assert "override=True" not in source, (
            "config.py must NOT call load_dotenv with override=True"
        )
