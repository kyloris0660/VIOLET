"""Shared pytest fixtures for V.I.O.L.E.T. test suite.

Provides fixtures for test environment validation, read-only fixture
inspection, and test DB connection helpers.
"""
import importlib
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _reload_settings(tmp_path):
    """Reload config module with load_dotenv suppressed."""
    import dotenv
    import app.config as config_mod
    with patch.object(dotenv, "load_dotenv", lambda *a, **kw: None):
        importlib.reload(config_mod)
    config_mod._PROJECT_ROOT = tmp_path
    return config_mod.Settings()


@pytest.fixture
def reload_settings(tmp_path):
    """Yield a settings reloader bound to tmp_path."""
    def _reload(env_overrides: dict | None = None):
        env = {
            "VIOLET_ENV": "test",
            "POSTGRES_DB": "blombooru_test",
            "VIOLET_STORAGE_ROOT": str(tmp_path / "storage"),
            "TEST_DATABASE_URL": "",
        }
        if env_overrides:
            env.update(env_overrides)
        with patch.dict(os.environ, env, clear=False):
            return _reload_settings(tmp_path)
    return _reload


@pytest.fixture
def fixture_path():
    """Return the VioletTestFixture path if configured, else skip."""
    val = os.environ.get("VIOLET_TEST_FIXTURE_PATH", "").strip()
    if not val:
        pytest.skip("VIOLET_TEST_FIXTURE_PATH not set")
    p = Path(val)
    if not p.is_dir():
        pytest.skip(f"VioletTestFixture dir not found: {p}")
    return p


@pytest.fixture
def fixture_counts(fixture_path):
    """Count supported images per subfolder — read-only, never mutates."""
    counts = {}
    for subfolder in ("anime", "non_anime", "mixed"):
        sub = fixture_path / subfolder
        if sub.is_dir():
            n = sum(
                1 for f in sub.iterdir()
                if f.is_file() and not f.name.startswith(".")
                and f.suffix.lower() in SUPPORTED_EXTENSIONS
            )
            counts[subfolder] = n
        else:
            counts[subfolder] = 0
    counts["total"] = sum(counts.values())
    return counts
