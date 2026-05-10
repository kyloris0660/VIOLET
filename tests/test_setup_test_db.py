"""Tests for scripts/setup_test_db.py — forbidden name gate and explicit migration URL."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import setup_test_db


class TestGetTestDbName:

    def test_default_returns_blombooru_test(self):
        with patch.dict(os.environ, {"POSTGRES_DB": "blombooru_test"}, clear=False):
            assert setup_test_db._get_test_db_name() == "blombooru_test"

    @pytest.mark.parametrize("name", ["blombooru", "production", "main", "postgres"])
    def test_rejects_forbidden_names(self, name):
        with patch.dict(os.environ, {"POSTGRES_DB": name}, clear=False):
            with pytest.raises(SystemExit):
                setup_test_db._get_test_db_name()

    @pytest.mark.parametrize("name", ["Blombooru", "PRODUCTION", "Main", "POSTGRES"])
    def test_rejects_forbidden_names_case_insensitive(self, name):
        with patch.dict(os.environ, {"POSTGRES_DB": name}, clear=False):
            with pytest.raises(SystemExit):
                setup_test_db._get_test_db_name()

    def test_accepts_valid_test_name(self):
        with patch.dict(os.environ, {"POSTGRES_DB": "my_test_db"}, clear=False):
            assert setup_test_db._get_test_db_name() == "my_test_db"


class TestBuildMigrateUrl:

    def test_uses_explicit_test_db(self):
        env = {"POSTGRES_HOST": "testhost", "POSTGRES_PORT": "9999",
               "POSTGRES_USER": "testuser", "POSTGRES_PASSWORD": "testpass"}
        with patch.dict(os.environ, env, clear=False):
            url = setup_test_db._build_migrate_url("blombooru_test")
        assert url.database == "blombooru_test"
        assert url.host == "testhost"
        assert url.port == 9999
        assert url.username == "testuser"

    def test_rejects_forbidden_db_in_url(self):
        with pytest.raises(SystemExit):
            setup_test_db._build_migrate_url("blombooru")

    def test_env_postgres_db_blombooru_cannot_override(self):
        """Even if POSTGRES_DB=blombooru in env, _build_migrate_url uses the
        explicit test_db argument, not the env var."""
        env = {"POSTGRES_DB": "blombooru", "POSTGRES_HOST": "localhost",
               "POSTGRES_PORT": "5432", "POSTGRES_USER": "postgres",
               "POSTGRES_PASSWORD": ""}
        with patch.dict(os.environ, env, clear=False):
            url = setup_test_db._build_migrate_url("blombooru_test")
        assert url.database == "blombooru_test"

    def test_url_drivername_is_postgresql(self):
        with patch.dict(os.environ, {}, clear=False):
            url = setup_test_db._build_migrate_url("blombooru_test")
        assert str(url).startswith("postgresql://")
