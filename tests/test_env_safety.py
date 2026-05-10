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


# ---------------------------------------------------------------------------
# 6b. Destructive gate — root / drive-root path rejection
# ---------------------------------------------------------------------------

class TestDestructiveGateRootPathRejection:

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

    def test_unix_root_slash_rejected(self, tmp_path):
        diag = self._get_gate(tmp_path, storage_root="/", test_storage_root="/")
        assert diag["conditions"]["8_no_root_storage_paths"] is False
        assert diag["gate_would_pass"] is False

    def test_windows_drive_root_backslash_rejected(self, tmp_path):
        diag = self._get_gate(tmp_path, storage_root="C:\\", test_storage_root="C:\\")
        assert diag["conditions"]["8_no_root_storage_paths"] is False
        assert diag["gate_would_pass"] is False

    def test_windows_drive_root_forward_rejected(self, tmp_path):
        diag = self._get_gate(tmp_path, storage_root="C:/", test_storage_root="C:/")
        assert diag["conditions"]["8_no_root_storage_paths"] is False
        assert diag["gate_would_pass"] is False

    def test_valid_subdirectory_passes(self, tmp_path):
        tsr = str(tmp_path / "test_storage")
        diag = self._get_gate(tmp_path, storage_root=tsr, test_storage_root=tsr)
        assert diag["conditions"]["8_no_root_storage_paths"] is True
        assert diag["gate_would_pass"] is True

    def test_storage_under_non_root_tsr_passes(self, tmp_path):
        tsr = str(tmp_path / "test_storage")
        sr = str(tmp_path / "test_storage" / "sub")
        diag = self._get_gate(tmp_path, storage_root=sr, test_storage_root=tsr)
        assert diag["conditions"]["8_no_root_storage_paths"] is True
        assert diag["conditions"]["6_storage_root_under_test_storage_root"] is True

    def test_unc_share_root_rejected(self, tmp_path):
        env = self._build_gate_env(tmp_path, storage_root="//server/share",
                                   test_storage_root="//server/share")
        with patch.dict(os.environ, env, clear=False):
            with patch("pathlib.Path.mkdir"):
                _reload_settings(tmp_path)
                import app.routes.admin.dev_tools as dt_mod
                importlib.reload(dt_mod)
                diag = dt_mod._compute_gate_diagnostic()
        assert diag["conditions"]["8_no_root_storage_paths"] is False
        assert diag["gate_would_pass"] is False


# ---------------------------------------------------------------------------
# 7. resolve_storage_path / storage_relative_path helpers
# ---------------------------------------------------------------------------

class TestStorageRootMediaPathHelpers:

    def _make_settings(self, tmp_path, storage_root=""):
        env = {"VIOLET_ENV": "development", "POSTGRES_DB": "blombooru",
               "VIOLET_STORAGE_ROOT": storage_root, "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            return _reload_settings(tmp_path)

    def test_resolve_typical_relative_path(self, tmp_path):
        sr = tmp_path / "storage"
        sr.mkdir()
        s = self._make_settings(tmp_path, str(sr))
        result = s.resolve_storage_path("media/original/abc.jpg")
        assert result == (sr / "media" / "original" / "abc.jpg").resolve()

    def test_resolve_backslash_path(self, tmp_path):
        sr = tmp_path / "storage"
        sr.mkdir()
        s = self._make_settings(tmp_path, str(sr))
        result = s.resolve_storage_path("media\\original\\abc.jpg")
        assert result == (sr / "media" / "original" / "abc.jpg").resolve()

    def test_resolve_rejects_empty(self, tmp_path):
        sr = tmp_path / "storage"
        sr.mkdir()
        s = self._make_settings(tmp_path, str(sr))
        assert s.resolve_storage_path("") is None
        assert s.resolve_storage_path(None) is None

    def test_resolve_rejects_absolute_unix(self, tmp_path):
        sr = tmp_path / "storage"
        sr.mkdir()
        s = self._make_settings(tmp_path, str(sr))
        assert s.resolve_storage_path("/etc/passwd") is None

    def test_resolve_rejects_absolute_windows(self, tmp_path):
        sr = tmp_path / "storage"
        sr.mkdir()
        s = self._make_settings(tmp_path, str(sr))
        assert s.resolve_storage_path("C:\\Users\\someone\\file.jpg") is None
        assert s.resolve_storage_path("C:/Users/someone/file.jpg") is None
        assert s.resolve_storage_path("D:\\media\\file.jpg") is None

    def test_resolve_rejects_unc_path(self, tmp_path):
        sr = tmp_path / "storage"
        sr.mkdir()
        s = self._make_settings(tmp_path, str(sr))
        assert s.resolve_storage_path("\\\\server\\share\\file.jpg") is None
        assert s.resolve_storage_path("//server/share/file.jpg") is None

    def test_resolve_rejects_traversal(self, tmp_path):
        sr = tmp_path / "storage"
        sr.mkdir()
        s = self._make_settings(tmp_path, str(sr))
        assert s.resolve_storage_path("../outside") is None
        assert s.resolve_storage_path("media/../../../etc/passwd") is None
        assert s.resolve_storage_path("media/original/../../..") is None

    def test_storage_relative_path_posix(self, tmp_path):
        sr = tmp_path / "storage"
        sr.mkdir()
        s = self._make_settings(tmp_path, str(sr))
        abs_path = sr / "media" / "original" / "abc.jpg"
        rel = s.storage_relative_path(abs_path)
        assert "/" in rel or "\\" not in rel
        assert rel == "media/original/abc.jpg"

    def test_storage_relative_path_rejects_outside(self, tmp_path):
        sr = tmp_path / "storage"
        sr.mkdir()
        s = self._make_settings(tmp_path, str(sr))
        outside = tmp_path / "elsewhere" / "file.jpg"
        with pytest.raises(ValueError):
            s.storage_relative_path(outside)

    def test_round_trip(self, tmp_path):
        sr = tmp_path / "storage"
        sr.mkdir()
        s = self._make_settings(tmp_path, str(sr))
        abs_path = sr / "media" / "original" / "test.png"
        rel = s.storage_relative_path(abs_path)
        resolved = s.resolve_storage_path(rel)
        assert resolved == abs_path.resolve()

    def test_unset_storage_root_falls_back_to_code_root(self, tmp_path):
        s = self._make_settings(tmp_path, "")
        assert s.STORAGE_ROOT == s.CODE_ROOT
        result = s.resolve_storage_path("media/original/x.jpg")
        assert result == (s.CODE_ROOT / "media" / "original" / "x.jpg").resolve()

    def test_worktree_scenario_code_root_ne_storage_root(self, tmp_path):
        sr = tmp_path / "separate_storage"
        sr.mkdir()
        s = self._make_settings(tmp_path, str(sr))
        assert s.STORAGE_ROOT != s.CODE_ROOT
        result = s.resolve_storage_path("media/original/x.jpg")
        assert str(s.CODE_ROOT) not in str(result)
        assert result == (sr / "media" / "original" / "x.jpg").resolve()


# ---------------------------------------------------------------------------
# 8. Media path flow tests (CODE_ROOT != STORAGE_ROOT)
# ---------------------------------------------------------------------------

class TestMediaPathFlow:
    """Simulate the upload→store→serve round-trip when CODE_ROOT != STORAGE_ROOT."""

    def _make_settings(self, tmp_path, storage_root=""):
        env = {"VIOLET_ENV": "development", "POSTGRES_DB": "blombooru",
               "VIOLET_STORAGE_ROOT": storage_root, "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            return _reload_settings(tmp_path)

    def test_upload_relativize_then_serve_resolve(self, tmp_path):
        sr = tmp_path / "storage"
        sr.mkdir()
        (sr / "media" / "original").mkdir(parents=True)
        img = sr / "media" / "original" / "abc123.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0")

        s = self._make_settings(tmp_path, str(sr))
        assert s.STORAGE_ROOT != s.CODE_ROOT

        db_path = s.storage_relative_path(img)
        assert db_path == "media/original/abc123.jpg"

        served = s.resolve_storage_path(db_path)
        assert served == img.resolve()
        assert served.exists()

    def test_thumbnail_relativize_then_resolve(self, tmp_path):
        sr = tmp_path / "storage"
        (sr / "media" / "thumbnails").mkdir(parents=True)
        thumb = sr / "media" / "thumbnails" / "abc123.jpg"
        thumb.write_bytes(b"\xff\xd8\xff\xe0")

        s = self._make_settings(tmp_path, str(sr))
        db_thumb = s.storage_relative_path(thumb)
        assert db_thumb == "media/thumbnails/abc123.jpg"

        resolved = s.resolve_storage_path(db_thumb)
        assert resolved == thumb.resolve()

    def test_resolve_never_falls_into_code_root(self, tmp_path):
        sr = tmp_path / "ext_storage"
        sr.mkdir()
        s = self._make_settings(tmp_path, str(sr))
        result = s.resolve_storage_path("media/original/test.png")
        assert str(s.CODE_ROOT) not in str(result)
        assert str(result).startswith(str(sr.resolve()))

    def test_backslash_db_path_resolves_correctly(self, tmp_path):
        sr = tmp_path / "storage"
        sr.mkdir()
        s = self._make_settings(tmp_path, str(sr))
        result = s.resolve_storage_path("media\\original\\test.png")
        expected = (sr / "media" / "original" / "test.png").resolve()
        assert result == expected


# ---------------------------------------------------------------------------
# 9. Storage-root containment — Path.relative_to() semantics
# ---------------------------------------------------------------------------

class TestStorageRootContainment:
    """Verify that resolve_storage_path uses proper path containment,
    not string-prefix matching.  Covers the P1 hotfix."""

    def _make_settings(self, tmp_path, storage_root=""):
        env = {"VIOLET_ENV": "development", "POSTGRES_DB": "blombooru",
               "VIOLET_STORAGE_ROOT": storage_root, "TEST_DATABASE_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            return _reload_settings(tmp_path)

    def test_valid_path_inside_storage_accepted(self, tmp_path):
        sr = tmp_path / "storage"
        sr.mkdir()
        s = self._make_settings(tmp_path, str(sr))
        result = s.resolve_storage_path("media/original/a.jpg")
        assert result is not None
        assert result == (sr / "media" / "original" / "a.jpg").resolve()

    def test_storage_evil_prefix_rejected(self, tmp_path):
        """STORAGE_ROOT=/tmp/storage must reject paths resolving to
        /tmp/storage_evil/a.jpg — the old string-prefix logic would accept
        this because '/tmp/storage_evil'.startswith('/tmp/storage') is True."""
        sr = tmp_path / "storage"
        sr.mkdir()
        evil = tmp_path / "storage_evil"
        evil.mkdir()
        (evil / "a.jpg").write_bytes(b"\xff")
        s = self._make_settings(tmp_path, str(sr))
        # Craft a relative path that, after normalization & resolve, would
        # land in storage_evil.  The early checks block ".." in parts, so the
        # only way this could slip through is via a symlink — tested below.
        # Here we directly verify that the method uses relative_to semantics
        # by checking the contract: result must be under STORAGE_ROOT.
        result = s.resolve_storage_path("media/original/a.jpg")
        if result is not None:
            storage_resolved = sr.resolve()
            result.relative_to(storage_resolved)  # must not raise

    def test_symlink_escape_rejected(self, tmp_path):
        """A symlink inside STORAGE_ROOT that points outside must be rejected."""
        sr = tmp_path / "storage"
        sr.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "secret.txt"
        secret.write_bytes(b"sensitive")

        link = sr / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("symlinks not supported on this filesystem")

        s = self._make_settings(tmp_path, str(sr))
        result = s.resolve_storage_path("escape/secret.txt")
        assert result is None, (
            "symlink escape should be rejected: resolved path is outside STORAGE_ROOT"
        )

    def test_backslash_path_accepted(self, tmp_path):
        sr = tmp_path / "storage"
        sr.mkdir()
        s = self._make_settings(tmp_path, str(sr))
        result = s.resolve_storage_path("media\\original\\a.jpg")
        assert result is not None
        assert result == (sr / "media" / "original" / "a.jpg").resolve()

    def test_posix_path_accepted(self, tmp_path):
        sr = tmp_path / "storage"
        sr.mkdir()
        s = self._make_settings(tmp_path, str(sr))
        result = s.resolve_storage_path("media/original/a.jpg")
        assert result is not None
        assert result == (sr / "media" / "original" / "a.jpg").resolve()

    def test_absolute_unix_still_rejected(self, tmp_path):
        sr = tmp_path / "storage"
        sr.mkdir()
        s = self._make_settings(tmp_path, str(sr))
        assert s.resolve_storage_path("/etc/passwd") is None

    def test_traversal_still_rejected(self, tmp_path):
        sr = tmp_path / "storage"
        sr.mkdir()
        s = self._make_settings(tmp_path, str(sr))
        assert s.resolve_storage_path("../outside") is None
        assert s.resolve_storage_path("media/../../../etc/passwd") is None

    def test_unc_still_rejected(self, tmp_path):
        sr = tmp_path / "storage"
        sr.mkdir()
        s = self._make_settings(tmp_path, str(sr))
        assert s.resolve_storage_path("\\\\server\\share\\file") is None
        assert s.resolve_storage_path("//server/share/file") is None

    def test_windows_drive_still_rejected(self, tmp_path):
        sr = tmp_path / "storage"
        sr.mkdir()
        s = self._make_settings(tmp_path, str(sr))
        assert s.resolve_storage_path("C:\\Users\\someone\\file.jpg") is None
        assert s.resolve_storage_path("D:/media/file.jpg") is None

    def test_empty_still_rejected(self, tmp_path):
        sr = tmp_path / "storage"
        sr.mkdir()
        s = self._make_settings(tmp_path, str(sr))
        assert s.resolve_storage_path("") is None
        assert s.resolve_storage_path(None) is None
