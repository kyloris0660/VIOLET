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
    "code_root", "storage_root", "git_sha", "git_branch",
    "pid", "port", "worktree_path", "deployment_type",
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
                    app_port="8011", worktree_path=None):
    """Call the endpoint function directly, bypassing FastAPI routing."""
    from app.routes.system import get_server_identity, detect_deployment_type

    mock_settings = MagicMock()
    mock_settings.VIOLET_ENV = violet_env
    mock_settings.CODE_ROOT = Path(code_root)
    mock_settings.STORAGE_ROOT = Path(storage_root)
    mock_settings.WORKTREE_PATH = worktree_path

    env = {
        "POSTGRES_DB": db_name,
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
        return asyncio.get_event_loop().run_until_complete(
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
        mock_settings.WORKTREE_PATH = None

        def mock_check_output_fail(cmd, **kwargs):
            raise FileNotFoundError("git not found")

        env = {"POSTGRES_DB": "blombooru_test", "APP_PORT": "8000"}

        with patch.dict(os.environ, env, clear=False), \
             patch("app.routes.system.settings", mock_settings), \
             patch("subprocess.check_output", side_effect=mock_check_output_fail):
            from app.routes.system import get_server_identity
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                get_server_identity(current_user={"username": "admin"})
            )

        assert result["git_sha"] == ""
        assert result["git_branch"] == ""
        assert "app_name" in result
