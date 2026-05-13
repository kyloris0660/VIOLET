"""Unit tests for GET /api/system/server-identity endpoint.

Verifies: all expected fields present, no secrets exposed, git failure
handled gracefully, deployment_type detection.
"""
import importlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXPECTED_FIELDS = {
    "app_name", "app_version", "violet_env", "db_name",
    "code_root", "storage_root", "original_dir", "thumbnail_dir",
    "storage_root_explicitly_set",
    "git_sha", "git_branch",
    "pid", "port", "worktree_path", "deployment_type",
    "python_executable", "python_version", "python_prefix",
    "python_base_prefix", "is_venv",
}

_SECRET_PATTERNS = [
    "TAG_TRANSLATION_LLM_API_KEY",
    "POSTGRES_PASSWORD",
    "SECRET_KEY",
    "JWT_SECRET",
    "sk-",
    "password",
]


def _build_identity(*, violet_env="test", db_name="blombooru_test",
                    code_root="C:\\project", storage_root="C:\\storage",
                    git_sha="abc1234", git_branch="main",
                    app_port="8011", worktree_path=None,
                    storage_root_explicitly_set=True):
    """Call the endpoint function directly, bypassing FastAPI routing."""
    from app.routes.system import get_server_identity, detect_deployment_type

    mock_settings = MagicMock()
    mock_settings.VIOLET_ENV = violet_env
    mock_settings.CODE_ROOT = Path(code_root)
    mock_settings.STORAGE_ROOT = Path(storage_root)
    mock_settings.ORIGINAL_DIR = Path(storage_root) / "media" / "original"
    mock_settings.THUMBNAIL_DIR = Path(storage_root) / "media" / "thumbnails"
    mock_settings.STORAGE_ROOT_EXPLICITLY_SET = storage_root_explicitly_set
    mock_settings.WORKTREE_PATH = worktree_path
    mock_settings.DB_NAME = db_name

    env = {
        "APP_PORT": app_port,
        "TAG_TRANSLATION_LLM_API_KEY": "sk-secret-key-12345",
        "POSTGRES_PASSWORD": "hunter2",
        "SECRET_KEY": "supersecret",
    }

    def mock_check_output(cmd, **kwargs):
        if "rev-parse" in cmd and "--short" in cmd:
            return git_sha + "\n"
        if "rev-parse" in cmd and "--abbrev-ref" in cmd:
            return git_branch + "\n"
        raise FileNotFoundError("git not found")

    with patch.dict(os.environ, env, clear=False), \
         patch("app.routes.system.settings", mock_settings), \
         patch("subprocess.check_output", side_effect=mock_check_output):
        import asyncio
        return asyncio.run(
            get_server_identity(current_user={"username": "admin"})
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestServerIdentityFields:

    def test_all_expected_fields_present(self):
        result = _build_identity()
        assert _EXPECTED_FIELDS == set(result.keys())

    def test_app_name(self):
        result = _build_identity()
        assert result["app_name"] == "V.I.O.L.E.T."

    def test_violet_env(self):
        result = _build_identity(violet_env="test")
        assert result["violet_env"] == "test"

    def test_db_name(self):
        result = _build_identity(db_name="blombooru_test")
        assert result["db_name"] == "blombooru_test"

    def test_code_root(self):
        result = _build_identity(code_root="C:\\my\\project")
        assert result["code_root"] == "C:\\my\\project"

    def test_storage_root(self):
        result = _build_identity(storage_root="C:\\my\\storage")
        assert result["storage_root"] == "C:\\my\\storage"

    def test_original_dir(self):
        result = _build_identity(storage_root="C:\\my\\storage")
        assert result["original_dir"] == str(Path("C:\\my\\storage") / "media" / "original")

    def test_thumbnail_dir(self):
        result = _build_identity(storage_root="C:\\my\\storage")
        assert result["thumbnail_dir"] == str(Path("C:\\my\\storage") / "media" / "thumbnails")

    def test_storage_root_explicitly_set_true(self):
        result = _build_identity(storage_root_explicitly_set=True)
        assert result["storage_root_explicitly_set"] is True

    def test_storage_root_explicitly_set_false(self):
        result = _build_identity(storage_root_explicitly_set=False)
        assert result["storage_root_explicitly_set"] is False

    def test_git_sha(self):
        result = _build_identity(git_sha="fe81f6b")
        assert result["git_sha"] == "fe81f6b"

    def test_git_branch(self):
        result = _build_identity(git_branch="feature/test")
        assert result["git_branch"] == "feature/test"

    def test_pid_is_int(self):
        result = _build_identity()
        assert isinstance(result["pid"], int)
        assert result["pid"] > 0

    def test_port(self):
        result = _build_identity(app_port="8011")
        assert result["port"] == 8011

    def test_worktree_path_none(self):
        result = _build_identity(worktree_path=None)
        assert result["worktree_path"] is None

    def test_worktree_path_set(self):
        result = _build_identity(worktree_path="C:\\worktrees\\abc")
        assert result["worktree_path"] == "C:\\worktrees\\abc"

    def test_deployment_type_local(self):
        with patch("app.routes.system.is_running_in_docker", return_value=False):
            result = _build_identity()
        assert result["deployment_type"] == "local"


class TestServerIdentityPythonFields:

    def test_python_executable_is_sys_executable(self):
        result = _build_identity()
        assert result["python_executable"] == sys.executable

    def test_python_version_format(self):
        result = _build_identity()
        version = result["python_version"]
        parts = version.split(".")
        assert len(parts) == 3, f"Expected major.minor.micro, got {version}"
        assert all(p.isdigit() for p in parts), f"Non-numeric version parts: {version}"

    def test_python_version_matches_sys(self):
        result = _build_identity()
        expected = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        assert result["python_version"] == expected

    def test_python_prefix_is_string(self):
        result = _build_identity()
        assert isinstance(result["python_prefix"], str)
        assert len(result["python_prefix"]) > 0

    def test_python_base_prefix_is_string(self):
        result = _build_identity()
        assert isinstance(result["python_base_prefix"], str)
        assert len(result["python_base_prefix"]) > 0

    def test_is_venv_is_bool(self):
        result = _build_identity()
        assert isinstance(result["is_venv"], bool)

    def test_is_venv_derivation(self):
        """is_venv must be True iff sys.prefix != sys.base_prefix."""
        result = _build_identity()
        assert result["is_venv"] == (sys.prefix != sys.base_prefix)


class TestServerIdentityNoSecrets:

    def test_no_api_key_in_response(self):
        result = _build_identity()
        response_str = str(result)
        for pattern in _SECRET_PATTERNS:
            assert pattern not in response_str, f"Secret pattern '{pattern}' found in response"

    def test_no_env_dump(self):
        result = _build_identity()
        assert "TAG_TRANSLATION_LLM_API_KEY" not in str(result)
        assert "POSTGRES_PASSWORD" not in str(result)
        assert "hunter2" not in str(result)
        assert "sk-secret-key-12345" not in str(result)

    def test_field_list_is_exact(self):
        result = _build_identity()
        assert set(result.keys()) == _EXPECTED_FIELDS


class TestServerIdentityGitFailure:

    def test_git_not_found(self):
        mock_settings = MagicMock()
        mock_settings.VIOLET_ENV = "test"
        mock_settings.CODE_ROOT = Path("C:\\project")
        mock_settings.STORAGE_ROOT = Path("C:\\storage")
        mock_settings.ORIGINAL_DIR = Path("C:\\storage\\media\\original")
        mock_settings.THUMBNAIL_DIR = Path("C:\\storage\\media\\thumbnails")
        mock_settings.STORAGE_ROOT_EXPLICITLY_SET = False
        mock_settings.WORKTREE_PATH = None
        mock_settings.DB_NAME = "blombooru_test"

        def mock_check_output_fail(cmd, **kwargs):
            raise FileNotFoundError("git not found")

        env = {"APP_PORT": "8000"}

        with patch.dict(os.environ, env, clear=False), \
             patch("app.routes.system.settings", mock_settings), \
             patch("subprocess.check_output", side_effect=mock_check_output_fail):
            from app.routes.system import get_server_identity
            import asyncio
            result = asyncio.run(
                get_server_identity(current_user={"username": "admin"})
            )

        assert result["git_sha"] == ""
        assert result["git_branch"] == ""
        assert "app_name" in result


class TestCheckScriptNormalizePath:
    """Tests for the normalize_path helper in check_test_server_identity.py."""

    def test_normalize_path_import(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from check_test_server_identity import normalize_path
        # Basic identity: normalizing a simple absolute path returns something non-empty
        result = normalize_path("C:\\Users\\test")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_normalize_path_case_insensitive_on_windows(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from check_test_server_identity import normalize_path
        import platform
        if platform.system() == "Windows":
            assert normalize_path("C:\\Users\\TEST") == normalize_path("C:\\users\\test")

    def test_normalize_path_trailing_separator(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from check_test_server_identity import normalize_path
        # Path with trailing separator should normalize same as without
        p1 = normalize_path("C:\\Users\\test")
        p2 = normalize_path("C:\\Users\\test\\")
        assert p1 == p2
