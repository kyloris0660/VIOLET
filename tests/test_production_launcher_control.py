from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import asyncio
from types import SimpleNamespace
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import violet_production_control as control  # noqa: E402
import backend.app.auth_middleware as auth_middleware  # noqa: E402
from backend.app import main as app_main  # noqa: E402
from backend.app.routes import health  # noqa: E402
from backend.app.auth_middleware import AuthMiddleware  # noqa: E402


def _write_fake_repo(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "run.py").write_text("print('placeholder')\n", encoding="utf-8")
    storage = tmp_path / "prodstore"
    data_dir = storage / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "settings.json").write_text(
        json.dumps(
            {
                "first_run": False,
                "database": {
                    "host": "localhost",
                    "port": 5432,
                    "name": "blombooru",
                    "user": "postgres",
                    "password": "",
                },
            }
        ),
        encoding="utf-8",
    )
    env = {
        "VIOLET_ENV": "production",
        "BLOMBOORU_DEBUG": "false",
        "VIOLET_STORAGE_ROOT": str(storage),
        "VIOLET_PRODUCTION_PYTHON": sys.executable,
        "APP_PORT": "8123",
    }
    (repo / ".env").write_text("\n".join(f"{key}={value}" for key, value in env.items()), encoding="utf-8")
    return repo, storage, env


def _write_fake_profile(
    repo: Path,
    storage: Path,
    *,
    profile_id: str = control.DEFAULT_PROFILE_ID,
    profile_env: str = "production",
    safe_startup: bool = True,
    db_user: str = "postgres",
    db_password: str = "",
    automation_flags: dict[str, bool] | None = None,
) -> Path:
    profile_path = repo / ".local_manifests" / "production_launcher" / "production-profile.json"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text(
        json.dumps(
            {
                "profile_id": profile_id,
                "env": profile_env,
                "repo_root": str(repo),
                "python": sys.executable,
                "app_port": 8123,
                "storage_root": str(storage),
                "db": {
                    "host": "localhost",
                    "port": 5432,
                    "name": "blombooru",
                    "user": db_user,
                    "password": db_password,
                },
                "safe_startup": safe_startup,
                "automation_flags": automation_flags or {
                    "dynamic_library_auto_sync": False,
                    "ai_auto_tag_after_import": False,
                    "content_classification_auto_after_import": False,
                    "tag_translation_auto": False,
                    "tag_translation_background": False,
                },
            }
        ),
        encoding="utf-8",
    )
    return profile_path


@pytest.fixture()
def safe_backends(monkeypatch):
    monkeypatch.setattr(control, "_append_launcher_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(control, "detect_git_worktree", lambda repo_root=control.ROOT: False)
    monkeypatch.setattr(control, "_storage_root_looks_production", lambda config: (True, "ok"))
    monkeypatch.setattr(control, "is_port_open", lambda port, host="127.0.0.1", timeout=0.5: False)
    monkeypatch.setattr(control, "port_owner_pid", lambda port: None)


def _managed_snapshot(pid: int, *, create_time: float = 100.0) -> control.ProcessSnapshot:
    return control.ProcessSnapshot(
        pid=pid,
        exists=True,
        command_line=f"{sys.executable} run.py",
        executable_path=sys.executable,
        create_time=create_time,
    )


def test_preflight_blocks_non_production_env(tmp_path, safe_backends):
    repo, _storage, env = _write_fake_repo(tmp_path)
    env["VIOLET_ENV"] = "development"

    result = control.preflight(
        repo_root=repo,
        base_env=env,
        db_check=lambda config: (True, "ok"),
        state_path=tmp_path / "state.json",
    )

    assert result.ok is False
    assert "violet_env_production" in result.errors


def test_profile_status_reports_missing_profile_without_using_development_env(tmp_path, safe_backends):
    repo, _storage, env = _write_fake_repo(tmp_path)
    env["VIOLET_ENV"] = "development"
    env["VIOLET_STORAGE_ROOT"] = ""

    result = control.profile_status(repo_root=repo, profile_id=control.DEFAULT_PROFILE_ID, base_env=env)

    assert result.ok is False
    assert result.status == "no_profile"
    assert "production_profile_exists" in result.errors
    assert result.data["profile"]["development_dotenv_required"] is False
    assert result.data["profile"]["development_dotenv_modified"] is False
    serialized = json.dumps(result.to_public_dict(), sort_keys=True)
    assert str(repo) not in serialized


def test_profile_preflight_uses_profile_not_development_dotenv(tmp_path, safe_backends):
    repo, storage, env = _write_fake_repo(tmp_path)
    env["VIOLET_ENV"] = "development"
    env["VIOLET_STORAGE_ROOT"] = ""
    settings_path = storage / "data" / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings["secret_key"] = "test-secret-key"
    settings_path.write_text(json.dumps(settings), encoding="utf-8")
    _write_fake_profile(repo, storage)

    result = control.preflight(
        repo_root=repo,
        base_env=env,
        profile_id=control.DEFAULT_PROFILE_ID,
        db_check=lambda config: (True, "ok"),
        state_path=tmp_path / "state.json",
    )

    assert result.ok is True
    assert result.data["config_source"] == "production_profile"
    assert result.data["env"] == "production"
    assert "violet_env_production" not in result.errors
    config = control.resolve_config(repo, base_env=env, profile_id=control.DEFAULT_PROFILE_ID)
    child_env = control._child_environment(config)
    assert child_env["VIOLET_ENV"] == "production"
    assert child_env["VIOLET_STORAGE_ROOT"] == str(storage)
    assert child_env["VIOLET_PRODUCTION_PROFILE_ACTIVE"] == "true"


def test_profile_launch_environment_strips_development_variables(tmp_path, safe_backends):
    repo, storage, env = _write_fake_repo(tmp_path)
    dotenv_before = (repo / ".env").read_text(encoding="utf-8")
    env.update(
        {
            "BLOMBOORU_REQUIRE_AUTH": "true",
            "OPENAI_API_KEY": "sk-development-secret",
            "LLM_PROVIDER": "openai",
            "REDIS_URL": "redis://localhost:6379/0",
            "VIOLET_RUN_REAL_E2E": "1",
            "VIOLET_ALLOW_DESTRUCTIVE_E2E": "1",
            "VIOLET_S3A_SYNC_ENABLED": "true",
            "DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED": "true",
            "TAG_TRANSLATION_BACKGROUND_ENABLED": "true",
        }
    )
    _write_fake_profile(repo, storage, db_user="violet_prod")

    config = control.resolve_config(repo, base_env=env, profile_id=control.DEFAULT_PROFILE_ID)
    child_env = control._child_environment(config)

    assert child_env["VIOLET_ENV"] == "production"
    assert child_env["VIOLET_STORAGE_ROOT"] == str(storage)
    assert child_env["POSTGRES_USER"] == "violet_prod"
    assert child_env["VIOLET_RUN_REAL_E2E"] == "false"
    assert child_env["VIOLET_ALLOW_DESTRUCTIVE_E2E"] == "false"
    assert child_env["DYNAMIC_LIBRARY_AUTO_SYNC_ENABLED"] == "false"
    assert child_env["TAG_TRANSLATION_BACKGROUND_ENABLED"] == "false"
    assert "BLOMBOORU_REQUIRE_AUTH" not in child_env
    assert "OPENAI_API_KEY" not in child_env
    assert "LLM_PROVIDER" not in child_env
    assert "REDIS_URL" not in child_env
    assert "VIOLET_S3A_SYNC_ENABLED" not in child_env
    assert (repo / ".env").read_text(encoding="utf-8") == dotenv_before


def test_profile_launch_sets_skip_dotenv_and_backend_profile_markers(tmp_path, safe_backends):
    repo, storage, env = _write_fake_repo(tmp_path)
    env["BLOMBOORU_REQUIRE_AUTH"] = "true"
    _write_fake_profile(repo, storage, db_user="violet_prod")

    config = control.resolve_config(repo, base_env=env, profile_id=control.DEFAULT_PROFILE_ID)
    child_env = control._child_environment(config)

    assert child_env["VIOLET_SKIP_DOTENV"] == "1"
    assert child_env["VIOLET_PRODUCTION_PROFILE_ACTIVE"] == "true"
    assert child_env["VIOLET_PRODUCTION_LAUNCHER_SAFE_STARTUP"] == "true"
    assert child_env["POSTGRES_USER"] == "violet_prod"
    assert "BLOMBOORU_REQUIRE_AUTH" not in child_env


def test_run_py_skip_dotenv_prevents_development_dotenv_reload(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copyfile(ROOT / "run.py", repo / "run.py")
    (repo / ".env").write_text("BLOMBOORU_REQUIRE_AUTH=true\nOPENAI_API_KEY=sk-development-secret\n", encoding="utf-8")
    script = "import os, run; assert 'BLOMBOORU_REQUIRE_AUTH' not in os.environ; assert 'OPENAI_API_KEY' not in os.environ"
    env = os.environ.copy()
    env["VIOLET_SKIP_DOTENV"] = "1"
    env.pop("BLOMBOORU_REQUIRE_AUTH", None)
    env.pop("OPENAI_API_KEY", None)

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_backend_config_skip_dotenv_keeps_profile_values(tmp_path):
    repo = tmp_path / "repo"
    config_dir = repo / "backend" / "app"
    config_dir.mkdir(parents=True)
    (repo / "backend" / "__init__.py").write_text("", encoding="utf-8")
    (config_dir / "__init__.py").write_text("", encoding="utf-8")
    shutil.copyfile(ROOT / "backend" / "app" / "config.py", config_dir / "config.py")
    storage = tmp_path / "profile-storage"
    (storage / "data").mkdir(parents=True)
    (storage / "data" / "settings.json").write_text(
        json.dumps({"first_run": False, "database": {"name": "settings_db"}, "secret_key": "stable"}),
        encoding="utf-8",
    )
    (repo / ".env").write_text("POSTGRES_DB=development_db\nBLOMBOORU_REQUIRE_AUTH=true\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(repo),
            "VIOLET_SKIP_DOTENV": "1",
            "VIOLET_ENV": "production",
            "VIOLET_PRODUCTION_PROFILE_ACTIVE": "true",
            "VIOLET_PRODUCTION_LAUNCHER_SAFE_STARTUP": "true",
            "VIOLET_STORAGE_ROOT": str(storage),
            "POSTGRES_DB": "profile_db",
            "POSTGRES_USER": "violet_prod",
            "POSTGRES_HOST": "localhost",
            "POSTGRES_PORT": "5432",
        }
    )
    script = "\n".join(
        [
            "import os",
            "from backend.app.config import settings",
            "assert settings.DB_NAME == 'profile_db'",
            "assert settings.DB_USER == 'violet_prod'",
            "assert 'BLOMBOORU_REQUIRE_AUTH' not in os.environ",
        ]
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_profile_status_and_discovery_expose_public_safe_db_user(tmp_path, safe_backends):
    repo, storage, env = _write_fake_repo(tmp_path)
    _write_fake_profile(repo, storage, db_user="violet_prod")

    status = control.profile_status(repo_root=repo, profile_id=control.DEFAULT_PROFILE_ID, base_env=env)
    discovered = control.profile_discover(repo_root=repo, profile_id=control.DEFAULT_PROFILE_ID)

    assert status.data["profile"]["db"]["user"] == "violet_prod"
    assert discovered.data["safe_inferred_fields"]["db_user"] == "violet_prod"
    serialized = json.dumps(status.to_public_dict(), sort_keys=True)
    assert "password" not in serialized.lower() or "password_present" in serialized


def test_profile_update_preserves_custom_db_user_when_unrelated_field_changes(tmp_path, safe_backends):
    repo, storage, env = _write_fake_repo(tmp_path)
    _write_fake_profile(repo, storage, db_user="violet_prod")

    result = control.profile_update(
        repo_root=repo,
        profile_id=control.DEFAULT_PROFILE_ID,
        base_env=env,
        updates={"app_port": "8124"},
    )

    profile_path = repo / ".local_manifests" / "production_launcher" / "production-profile.json"
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    assert result.ok is True
    assert payload["app_port"] == "8124"
    assert payload["db"]["user"] == "violet_prod"


def test_profile_update_stdin_json_accepts_password_payload_without_argv(tmp_path, safe_backends):
    repo, storage, env = _write_fake_repo(tmp_path)
    _write_fake_profile(repo, storage, db_user="violet_prod")

    payload, error = control._profile_update_stdin_payload(
        json.dumps({"db": {"password": "secret-db-password"}})
    )
    assert error is None
    result = control.profile_update(
        repo_root=repo,
        profile_id=control.DEFAULT_PROFILE_ID,
        base_env=env,
        updates=payload,
    )

    profile_path = repo / ".local_manifests" / "production_launcher" / "production-profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    assert result.data["profile"]["db"]["password_present"] is True
    assert profile["db"]["password"] == "secret-db-password"


def test_profile_mismatch_fails_closed_for_status_preflight_and_start(tmp_path, safe_backends, monkeypatch):
    repo, storage, env = _write_fake_repo(tmp_path)
    _write_fake_profile(repo, storage, profile_id="wrong-profile")

    status = control.profile_status(repo_root=repo, profile_id=control.DEFAULT_PROFILE_ID, base_env=env)
    preflight = control.preflight(
        repo_root=repo,
        base_env=env,
        profile_id=control.DEFAULT_PROFILE_ID,
        db_check=lambda config: (True, "ok"),
        state_path=tmp_path / "state.json",
    )
    monkeypatch.setattr(control.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("start must not launch on mismatched profile"))
    started = control.start_production(
        repo_root=repo,
        base_env=env,
        profile_id=control.DEFAULT_PROFILE_ID,
        state_path=tmp_path / "state.json",
        start_lock_path=tmp_path / "start.lock",
    )

    assert status.status == "profile_error"
    assert "profile_id_mismatch" in status.errors
    assert preflight.ok is False
    assert "production_profile_valid" in preflight.errors
    assert started.ok is False
    assert "production_profile_valid" in started.errors


def test_profile_repair_resets_invalid_invariants_and_flags(tmp_path, safe_backends):
    repo, storage, env = _write_fake_repo(tmp_path)
    _write_fake_profile(
        repo,
        storage,
        profile_id="wrong-profile",
        profile_env="development",
        safe_startup=False,
        automation_flags={
            "dynamic_library_auto_sync": True,
            "ai_auto_tag_after_import": True,
            "content_classification_auto_after_import": True,
            "tag_translation_auto": True,
            "tag_translation_background": True,
        },
    )

    result = control.profile_repair(repo_root=repo, profile_id=control.DEFAULT_PROFILE_ID)

    profile_path = repo / ".local_manifests" / "production_launcher" / "production-profile.json"
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    assert result.status in {"ready", "profile_incomplete"}
    assert payload["profile_id"] == control.DEFAULT_PROFILE_ID
    assert payload["env"] == "production"
    assert payload["safe_startup"] is True
    assert all(value is False for value in payload["automation_flags"].values())


def test_profile_repair_bootstraps_local_storage_and_db_from_dotenv_and_settings(tmp_path, safe_backends):
    repo, storage, _env = _write_fake_repo(tmp_path)
    settings_path = storage / "data" / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings["database"] = {
        "host": "db.local",
        "port": 5544,
        "name": "prod_db",
        "user": "violet_prod",
        "password": "local-private-db-value",
    }
    settings_path.write_text(json.dumps(settings), encoding="utf-8")
    (repo / ".env").write_text(
        "\n".join(
            [
                "VIOLET_STORAGE_ROOT=" + str(storage),
                "POSTGRES_DB=dotenv_db",
                "POSTGRES_USER=dotenv_user",
                "POSTGRES_PASSWORD=dotenv-private",
                "APP_PORT=8129",
            ]
        ),
        encoding="utf-8",
    )

    result = control.profile_repair(repo_root=repo, profile_id=control.DEFAULT_PROFILE_ID)

    profile_path = repo / ".local_manifests" / "production_launcher" / "production-profile.json"
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    serialized_public = json.dumps(result.to_public_dict(), sort_keys=True)
    assert payload["storage_root"] == str(storage)
    assert payload["app_port"] == 8129
    assert payload["db"]["name"] == "prod_db"
    assert payload["db"]["user"] == "violet_prod"
    assert payload["db"]["password"] == "local-private-db-value"
    assert "local-private-db-value" not in serialized_public
    assert "dotenv-private" not in serialized_public


def test_profile_repair_can_infer_canonical_colocated_storage_from_settings(tmp_path, monkeypatch):
    repo, _storage, _env = _write_fake_repo(tmp_path)
    (repo / ".env").write_text("POSTGRES_DB=blombooru\n", encoding="utf-8")
    (repo / "data").mkdir()
    (repo / "data" / "settings.json").write_text(
        json.dumps({"first_run": False, "database": {"name": "blombooru", "user": "violet_prod"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(control, "detect_git_worktree", lambda repo_root=control.ROOT: False)

    result = control.profile_repair(repo_root=repo, profile_id=control.DEFAULT_PROFILE_ID)
    profile = json.loads((repo / ".local_manifests" / "production_launcher" / "production-profile.json").read_text(encoding="utf-8"))
    config = control.resolve_config(repo, profile_id=control.DEFAULT_PROFILE_ID)
    storage_ok, storage_message = control._storage_root_looks_production(config)

    assert profile["storage_root"] == str(repo)
    assert result.data["profile"]["storage_root_configured"] is True
    assert storage_ok is True
    assert "canonical production checkout" in storage_message


def test_colocated_storage_is_rejected_for_worktree(tmp_path, monkeypatch):
    repo, _storage, _env = _write_fake_repo(tmp_path)
    _write_fake_profile(repo, repo)
    monkeypatch.setattr(control, "detect_git_worktree", lambda repo_root=control.ROOT: True)
    monkeypatch.setattr(control, "_repo_root_is_codex_worktree", lambda repo_root: False)
    config = control.resolve_config(repo, profile_id=control.DEFAULT_PROFILE_ID)

    storage_ok, storage_message = control._storage_root_looks_production(config)

    assert storage_ok is False
    assert "worktree" in storage_message


def test_profile_preflight_blocks_missing_secret_key_to_avoid_settings_import_write(tmp_path, safe_backends):
    repo, storage, env = _write_fake_repo(tmp_path)
    _write_fake_profile(repo, storage)

    result = control.preflight(
        repo_root=repo,
        base_env=env,
        profile_id=control.DEFAULT_PROFILE_ID,
        db_check=lambda config: (True, "ok"),
        state_path=tmp_path / "state.json",
    )

    assert result.ok is False
    assert "settings_import_write_safe" in result.errors


def test_profile_update_writes_local_ignored_profile_without_touching_dotenv(tmp_path, safe_backends):
    repo, storage, env = _write_fake_repo(tmp_path)
    dotenv_before = (repo / ".env").read_text(encoding="utf-8")

    result = control.profile_update(
        repo_root=repo,
        profile_id=control.DEFAULT_PROFILE_ID,
        base_env=env,
        updates={
            "storage_root": str(storage),
            "app_port": "8124",
            "db": {"host": "localhost", "port": "5432", "name": "blombooru", "user": "postgres"},
        },
    )

    assert result.data["profile"]["profile_path_local_ignored"] is True
    assert (repo / ".env").read_text(encoding="utf-8") == dotenv_before
    profile_path = repo / ".local_manifests" / "production_launcher" / "production-profile.json"
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    assert payload["storage_root"] == str(storage)
    assert payload["app_port"] == "8124"


def test_preflight_blocks_unknown_process_on_target_port(tmp_path, safe_backends, monkeypatch):
    repo, _storage, env = _write_fake_repo(tmp_path)
    monkeypatch.setattr(control, "is_port_open", lambda port, host="127.0.0.1", timeout=0.5: True)

    result = control.preflight(
        repo_root=repo,
        base_env=env,
        db_check=lambda config: (True, "ok"),
        state_path=tmp_path / "missing-state.json",
    )

    assert result.ok is False
    assert "target_port_available_or_managed" in result.errors


def test_preflight_blocks_malformed_app_port(tmp_path, safe_backends):
    repo, _storage, env = _write_fake_repo(tmp_path)
    env["APP_PORT"] = "abc"

    result = control.preflight(
        repo_root=repo,
        base_env=env,
        db_check=lambda config: (True, "ok"),
        state_path=tmp_path / "state.json",
    )

    assert result.ok is False
    assert "app_port_resolved" in result.errors
    assert result.data["app_port_resolved"] is False


def test_preflight_blocks_malformed_postgres_port(tmp_path, safe_backends):
    repo, _storage, env = _write_fake_repo(tmp_path)
    env["POSTGRES_PORT"] = "abc"

    result = control.preflight(
        repo_root=repo,
        base_env=env,
        db_check=lambda config: (True, "ok"),
        state_path=tmp_path / "state.json",
    )

    assert result.ok is False
    assert "db_port_valid" in result.errors
    assert result.data["db_port_valid"] is False


def test_preflight_blocks_malformed_settings_db_port(tmp_path, safe_backends):
    repo, storage, env = _write_fake_repo(tmp_path)
    settings_path = storage / "data" / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings["database"]["port"] = "abc"
    settings_path.write_text(json.dumps(settings), encoding="utf-8")

    result = control.preflight(
        repo_root=repo,
        base_env=env,
        db_check=lambda config: (True, "ok"),
        state_path=tmp_path / "state.json",
    )

    assert result.ok is False
    assert "db_port_valid" in result.errors
    assert result.data["db_port_valid"] is False


def test_preflight_blocks_destructive_e2e_flag(tmp_path, safe_backends):
    repo, _storage, env = _write_fake_repo(tmp_path)
    env["VIOLET_ALLOW_DESTRUCTIVE_E2E"] = "1"

    result = control.preflight(
        repo_root=repo,
        base_env=env,
        db_check=lambda config: (True, "ok"),
        state_path=tmp_path / "state.json",
    )

    assert result.ok is False
    assert "destructive_e2e_disabled" in result.errors
    assert result.data["destructive_e2e_allowed"] is False


def test_preflight_blocks_real_e2e_flag(tmp_path, safe_backends):
    repo, _storage, env = _write_fake_repo(tmp_path)
    env["VIOLET_RUN_REAL_E2E"] = "true"

    result = control.preflight(
        repo_root=repo,
        base_env=env,
        db_check=lambda config: (True, "ok"),
        state_path=tmp_path / "state.json",
    )

    assert result.ok is False
    assert "dangerous_dev_test_flags_disabled" in result.errors
    assert result.data["destructive_e2e_allowed"] is False


def test_public_json_omits_raw_log_tail_with_paths_and_tokens(tmp_path, safe_backends, monkeypatch):
    repo, storage, env = _write_fake_repo(tmp_path)
    unsafe_lines = [
        f"STORAGE_ROOT={storage}\\private-file.jpg",
        "Authorization: Bearer sk-test-secret-token",
    ]
    monkeypatch.setattr(control, "tail_log", lambda lines=20: unsafe_lines)
    monkeypatch.setattr(control, "fetch_health", lambda url, timeout=2.0: None)

    result = control.status(repo_root=repo, base_env=env, state_path=tmp_path / "state.json")
    public = result.to_public_dict()
    serialized = json.dumps(public, sort_keys=True)

    assert "recent_log_tail" in result.data
    assert "recent_log_tail" not in serialized
    assert str(storage) not in serialized
    assert "sk-test-secret-token" not in serialized
    assert public["data"]["log_tail_in_public_json"] is False
    assert public["data"]["log_tail_redacted"] is True


def test_python_public_redaction_handles_forward_slash_windows_paths_and_private_values():
    text = (
        "Trace D:/Storage/private/file.jpg "
        "C:/Users/kyloris/.codex/worktrees/private/profile.json "
        "Authorization: Bearer sk-development-secret "
        "password=local-private-db-value"
    )

    redacted = control._redact_private_text(text)

    assert "D:/Storage" not in redacted
    assert "C:/Users" not in redacted
    assert "sk-development-secret" not in redacted
    assert "local-private-db-value" not in redacted
    assert control._redact_private_text("open http://127.0.0.1:8000") == "open http://127.0.0.1:8000"


def test_posix_port_owner_pid_uses_lsof_when_available(monkeypatch):
    monkeypatch.setattr(control.platform, "system", lambda: "Linux")

    def fake_run(command, **kwargs):
        assert command[0] == "lsof"
        return SimpleNamespace(stdout="p4321\n", returncode=0)

    monkeypatch.setattr(control.subprocess, "run", fake_run)

    assert control.port_owner_pid(8123) == 4321


def test_posix_port_owner_pid_falls_back_to_ss(monkeypatch):
    monkeypatch.setattr(control.platform, "system", lambda: "Linux")

    def fake_run(command, **kwargs):
        if command[0] == "lsof":
            raise FileNotFoundError("lsof")
        return SimpleNamespace(
            stdout='LISTEN 0 128 127.0.0.1:8123 0.0.0.0:* users:(("python",pid=9876,fd=7))\n',
            returncode=0,
        )

    monkeypatch.setattr(control.subprocess, "run", fake_run)

    assert control.port_owner_pid(8123) == 9876


def test_posix_unknown_port_owner_keeps_managed_process_unverified(tmp_path, safe_backends, monkeypatch):
    repo, storage, env = _write_fake_repo(tmp_path)
    _write_fake_profile(repo, storage)
    config = control.resolve_config(repo, base_env=env, profile_id=control.DEFAULT_PROFILE_ID)
    state = {
        "state_version": control.STATE_VERSION,
        "app_name": control.APP_NAME,
        "started_by": "violet_production_launcher",
        "pid": 1234,
        "pid_create_time": 100.0,
        "start_time": "1970-01-01T00:00:00+00:00",
        "repo_root": str(repo),
        "port": 8123,
        "env": "production",
    }
    monkeypatch.setattr(control, "process_snapshot", lambda pid: _managed_snapshot(pid))
    monkeypatch.setattr(control, "is_port_open", lambda port, host="127.0.0.1", timeout=0.5: True)
    monkeypatch.setattr(control, "port_owner_pid", lambda port: None)

    verified, reasons = control.verify_managed_process(state, config)

    assert verified is False
    assert "target_port_owner_unavailable" in reasons


def test_preflight_reports_explicit_startup_write_policy(tmp_path, safe_backends):
    repo, _storage, env = _write_fake_repo(tmp_path)

    result = control.preflight(
        repo_root=repo,
        base_env=env,
        db_check=lambda config: (True, "ok"),
        state_path=tmp_path / "state.json",
    )

    policy = result.data["startup_write_policy"]
    assert policy["normal_startup_maintenance_documented"] is True
    assert policy["schema_migration_allowed"] is False
    assert policy["destructive_cleanup_allowed"] is False
    assert policy["import_tagging_sync_jobs_allowed"] is False
    assert policy["operator_intent_required_for_startup_maintenance"] is True


def test_status_removes_stale_launcher_pid_state(tmp_path, safe_backends, monkeypatch):
    repo, _storage, env = _write_fake_repo(tmp_path)
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "state_version": control.STATE_VERSION,
                "app_name": control.APP_NAME,
                "started_by": "violet_production_launcher",
                "pid": 424242,
                "start_time": "1970-01-01T00:00:00+00:00",
                "repo_root": str(repo),
                "port": 8123,
                "env": "production",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(control, "process_exists", lambda pid: False)
    monkeypatch.setattr(control, "fetch_health", lambda url, timeout=2.0: None)

    result = control.status(repo_root=repo, base_env=env, state_path=state_path)

    assert result.ok is True
    assert result.data["running"] is False
    assert not state_path.exists()


def test_stop_managed_process_terminates_and_clears_state(tmp_path, safe_backends, monkeypatch):
    repo, _storage, env = _write_fake_repo(tmp_path)
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "state_version": control.STATE_VERSION,
                "app_name": control.APP_NAME,
                "started_by": "violet_production_launcher",
                "pid": 1234,
                "pid_create_time": 100.0,
                "start_time": "1970-01-01T00:00:00+00:00",
                "repo_root": str(repo),
                "port": 8123,
                "env": "production",
            }
        ),
        encoding="utf-8",
    )
    alive = {"value": True}
    calls: list[tuple[int, bool]] = []

    def fake_process_exists(pid: int) -> bool:
        return alive["value"] if pid == 1234 else False

    def fake_terminate(pid: int, force: bool) -> None:
        calls.append((pid, force))
        alive["value"] = False

    monkeypatch.setattr(control, "process_exists", fake_process_exists)
    monkeypatch.setattr(control, "process_snapshot", lambda pid: _managed_snapshot(pid))

    result = control.stop_production(repo_root=repo, base_env=env, state_path=state_path, terminate=fake_terminate)

    assert result.ok is True
    assert result.status == "stopped"
    assert calls == [(1234, False)]
    assert not state_path.exists()


def test_stop_refuses_reused_or_unverified_pid(tmp_path, safe_backends, monkeypatch):
    repo, _storage, env = _write_fake_repo(tmp_path)
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "state_version": control.STATE_VERSION,
                "app_name": control.APP_NAME,
                "started_by": "violet_production_launcher",
                "pid": 1234,
                "pid_create_time": 50.0,
                "start_time": "2026-01-01T00:00:00+00:00",
                "repo_root": str(repo),
                "port": 8123,
                "env": "production",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(control, "process_exists", lambda pid: pid == 1234)
    monkeypatch.setattr(control, "process_snapshot", lambda pid: _managed_snapshot(pid, create_time=50.0))

    def forbidden_terminate(pid: int, force: bool) -> None:
        raise AssertionError("unverified process must not be terminated")

    result = control.stop_production(repo_root=repo, base_env=env, state_path=state_path, terminate=forbidden_terminate)

    assert result.ok is False
    assert "unknown_or_unverified_process_refused" in result.errors
    assert "pid_created_before_launcher_state" in result.data["verification_failures"]


def test_posix_missing_create_time_can_verify_with_cwd_cmd_and_port(tmp_path, safe_backends, monkeypatch):
    repo, _storage, env = _write_fake_repo(tmp_path)
    config = control.resolve_config(repo, base_env=env)
    state = {
        "state_version": control.STATE_VERSION,
        "app_name": control.APP_NAME,
        "started_by": "violet_production_launcher",
        "pid": 1234,
        "start_time": "2026-01-01T00:00:00+00:00",
        "repo_root": str(repo),
        "port": 8123,
        "env": "production",
    }
    monkeypatch.setattr(control.platform, "system", lambda: "Linux")
    monkeypatch.setattr(control, "process_exists", lambda pid: pid == 1234)
    monkeypatch.setattr(control, "port_owner_pid", lambda port: 1234)
    monkeypatch.setattr(
        control,
        "process_snapshot",
        lambda pid: control.ProcessSnapshot(
            pid=pid,
            exists=True,
            command_line=f"{sys.executable} run.py",
            executable_path=sys.executable,
            create_time=None,
            cwd=str(repo),
        ),
    )

    verified, reasons = control.verify_managed_process(state, config)

    assert verified is True
    assert reasons == []


def test_posix_verification_refuses_mismatched_cwd_or_port(tmp_path, safe_backends, monkeypatch):
    repo, _storage, env = _write_fake_repo(tmp_path)
    other_repo = tmp_path / "other"
    other_repo.mkdir()
    config = control.resolve_config(repo, base_env=env)
    state = {
        "state_version": control.STATE_VERSION,
        "app_name": control.APP_NAME,
        "started_by": "violet_production_launcher",
        "pid": 1234,
        "start_time": "2026-01-01T00:00:00+00:00",
        "repo_root": str(repo),
        "port": 8123,
        "env": "production",
    }
    monkeypatch.setattr(control.platform, "system", lambda: "Linux")
    monkeypatch.setattr(control, "process_exists", lambda pid: pid == 1234)
    monkeypatch.setattr(control, "port_owner_pid", lambda port: 9999)
    monkeypatch.setattr(
        control,
        "process_snapshot",
        lambda pid: control.ProcessSnapshot(
            pid=pid,
            exists=True,
            command_line=f"{sys.executable} run.py",
            executable_path=sys.executable,
            create_time=None,
            cwd=str(other_repo),
        ),
    )

    verified, reasons = control.verify_managed_process(state, config)

    assert verified is False
    assert "process_cwd_mismatch" in reasons
    assert "target_port_owned_by_different_pid" in reasons


def test_stop_refuses_unknown_process_on_port(tmp_path, safe_backends, monkeypatch):
    repo, _storage, env = _write_fake_repo(tmp_path)
    monkeypatch.setattr(control, "is_port_open", lambda port, host="127.0.0.1", timeout=0.5: True)

    def forbidden_terminate(pid: int, force: bool) -> None:
        raise AssertionError("unknown process must not be terminated")

    result = control.stop_production(
        repo_root=repo,
        base_env=env,
        state_path=tmp_path / "missing-state.json",
        terminate=forbidden_terminate,
    )

    assert result.ok is False
    assert "unknown_process_refusal" in result.errors


def test_production_command_and_child_env_never_enable_debug(tmp_path, safe_backends):
    repo, _storage, env = _write_fake_repo(tmp_path)
    config = control.resolve_config(repo, base_env=env)

    command = control.production_command(config)
    child_env = control._child_environment(config)

    assert command[-1] == "run.py"
    assert "--debug" not in command
    assert child_env["VIOLET_ENV"] == "production"
    assert child_env["BLOMBOORU_DEBUG"] == "false"
    assert child_env["VIOLET_ALLOW_DESTRUCTIVE_E2E"] == "false"
    assert child_env["VIOLET_RUN_REAL_E2E"] == "false"
    assert child_env["TAG_TRANSLATION_BACKGROUND_ENABLED"] == "false"
    assert child_env[control.STARTUP_SAFE_MODE_ENV] == "true"


def test_start_returns_single_flight_status_when_lock_exists(tmp_path, safe_backends, monkeypatch):
    repo, _storage, env = _write_fake_repo(tmp_path)
    monkeypatch.setattr(
        control,
        "_acquire_start_lock",
        lambda path=control.START_LOCK_FILE: control.StartLockStatus(acquired=False, reason="active_start_lock"),
    )

    result = control.start_production(repo_root=repo, base_env=env, state_path=tmp_path / "state.json")

    assert result.ok is False
    assert result.status == "blocked"
    assert "start_already_in_progress" in result.errors


def test_stale_start_lock_with_dead_pid_is_reclaimed(tmp_path, safe_backends, monkeypatch):
    lock_path = tmp_path / "start.lock"
    lock_path.write_text(json.dumps({"pid": 987654, "created_at": control.now_iso()}), encoding="utf-8")
    monkeypatch.setattr(control, "process_exists", lambda pid: False)

    status = control._acquire_start_lock(lock_path)

    assert status.acquired is True
    assert status.stale_reclaimed is True
    control._release_start_lock(lock_path)


def test_active_start_lock_with_live_pid_blocks(tmp_path, safe_backends, monkeypatch):
    lock_path = tmp_path / "start.lock"
    lock_path.write_text(json.dumps({"pid": 1234, "created_at": control.now_iso()}), encoding="utf-8")
    monkeypatch.setattr(control, "process_exists", lambda pid: pid == 1234)
    monkeypatch.setattr(control, "process_command_line", lambda pid: f"{sys.executable} scripts/violet_production_control.py start")

    status = control._acquire_start_lock(lock_path)

    assert status.acquired is False
    assert status.reason == "active_start_lock"


def test_malformed_start_lock_is_reclaimed(tmp_path, safe_backends, monkeypatch):
    lock_path = tmp_path / "start.lock"
    lock_path.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(control, "process_exists", lambda pid: False)

    status = control._acquire_start_lock(lock_path)

    assert status.acquired is True
    assert status.stale_reclaimed is True
    control._release_start_lock(lock_path)


def test_start_reports_reclaimed_stale_lock(tmp_path, safe_backends, monkeypatch):
    repo, _storage, env = _write_fake_repo(tmp_path)
    env["VIOLET_ENV"] = "development"
    lock_path = tmp_path / "start.lock"
    lock_path.write_text(json.dumps({"pid": 987654, "created_at": control.now_iso()}), encoding="utf-8")
    monkeypatch.setattr(control, "process_exists", lambda pid: False)

    result = control.start_production(
        repo_root=repo,
        base_env=env,
        state_path=tmp_path / "state.json",
        start_lock_path=lock_path,
    )

    assert result.ok is False
    assert result.data["stale_lock_reclaimed"] is True


def test_status_json_summary_is_public_safe(tmp_path, safe_backends, monkeypatch):
    repo, storage, env = _write_fake_repo(tmp_path)
    monkeypatch.setattr(
        control,
        "fetch_health",
        lambda url, timeout=2.0: {
            "ok": True,
            "app_name": "V.I.O.L.E.T.",
            "env": "production",
            "debug": False,
            "db_reachable": True,
            "schema_compatible": True,
            "schema_status": "compatible",
            "storage_configured": True,
        },
    )

    summary = control.diagnostic_summary(repo)
    serialized = json.dumps(summary, sort_keys=True)

    assert summary["env"] == "production"
    assert summary["health_ok"] is True
    assert str(storage) not in serialized
    assert str(repo) not in serialized


def test_status_reports_managed_unhealthy_process_as_unhealthy(tmp_path, safe_backends, monkeypatch):
    repo, _storage, env = _write_fake_repo(tmp_path)
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "state_version": control.STATE_VERSION,
                "app_name": control.APP_NAME,
                "started_by": "violet_production_launcher",
                "pid": 1234,
                "pid_create_time": 100.0,
                "start_time": "1970-01-01T00:00:00+00:00",
                "repo_root": str(repo),
                "port": 8123,
                "env": "production",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(control, "process_exists", lambda pid: pid == 1234)
    monkeypatch.setattr(control, "process_snapshot", lambda pid: _managed_snapshot(pid))
    monkeypatch.setattr(control, "fetch_health", lambda url, timeout=2.0: None)

    result = control.status(repo_root=repo, base_env=env, state_path=state_path)

    assert result.status == "unhealthy"
    assert result.data["running"] is True
    assert result.data["health_ok"] is False


def test_safe_startup_lifespan_does_not_create_background_tasks(monkeypatch):
    created: list[object] = []
    monkeypatch.setenv("VIOLET_ENV", "production")
    monkeypatch.setenv("VIOLET_PRODUCTION_LAUNCHER_SAFE_STARTUP", "true")
    monkeypatch.setattr(app_main, "init_engine", lambda: object())
    monkeypatch.setattr(app_main.asyncio, "create_task", lambda coro: created.append(coro) or pytest.fail("safe startup must not create background tasks"))
    app_main.settings.settings["first_run"] = False

    async def exercise():
        app = SimpleNamespace(state=SimpleNamespace())
        async with app_main.lifespan(app):
            assert getattr(app.state, "violet_background_tasks", []) == []

    asyncio.run(exercise())
    assert created == []


def test_shutdown_helper_cancels_tracked_background_tasks():
    async def sleeper():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise

    async def exercise():
        app = SimpleNamespace(state=SimpleNamespace())
        task = app_main._track_background_task(app, sleeper())
        await app_main._cancel_background_tasks(app, timeout_seconds=1.0)
        return task.cancelled()

    assert asyncio.run(exercise()) is True


def test_health_reports_schema_compatible_true_without_writes(monkeypatch):
    statements: list[str] = []

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, statement):
            statements.append(str(statement))

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    fake_engine = FakeEngine()
    fake_settings = SimpleNamespace(
        IS_FIRST_RUN=False,
        STORAGE_ROOT_EXPLICITLY_SET=True,
        DEBUG=False,
        VIOLET_ENV="production",
    )
    monkeypatch.setattr(health.database, "engine", fake_engine)
    monkeypatch.setattr(health.database, "init_engine", lambda: fake_engine)
    monkeypatch.setattr(health, "settings", fake_settings)
    monkeypatch.setattr(
        health,
        "inspect",
        lambda engine: SimpleNamespace(
            get_table_names=lambda: list(health.REQUIRED_CORE_TABLES),
            get_columns=lambda table_name: [{"name": name} for name in health.REQUIRED_CORE_COLUMNS[table_name]],
        ),
    )

    import asyncio

    payload = asyncio.run(health.get_health())

    assert payload["ok"] is True
    assert payload["schema_compatible"] is True
    assert payload["schema_status"] == "compatible"
    assert statements == ["SELECT 1"]


def test_health_reports_schema_incompatible_when_core_table_missing(monkeypatch):
    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, statement):
            return None

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    fake_engine = FakeEngine()
    fake_settings = SimpleNamespace(
        IS_FIRST_RUN=False,
        STORAGE_ROOT_EXPLICITLY_SET=True,
        DEBUG=False,
        VIOLET_ENV="production",
    )
    tables = set(health.REQUIRED_CORE_TABLES)
    tables.remove("blombooru_media")
    monkeypatch.setattr(health.database, "engine", fake_engine)
    monkeypatch.setattr(health.database, "init_engine", lambda: fake_engine)
    monkeypatch.setattr(health, "settings", fake_settings)
    monkeypatch.setattr(
        health,
        "inspect",
        lambda engine: SimpleNamespace(
            get_table_names=lambda: list(tables),
            get_columns=lambda table_name: [{"name": name} for name in health.REQUIRED_CORE_COLUMNS.get(table_name, set())],
        ),
    )

    import asyncio

    payload = asyncio.run(health.get_health())

    assert payload["ok"] is False
    assert payload["db_reachable"] is True
    assert payload["schema_compatible"] is False
    assert payload["schema_status"] == "missing_required_tables"


def test_health_reports_schema_incompatible_when_core_column_missing(monkeypatch):
    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, statement):
            return None

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    fake_engine = FakeEngine()
    fake_settings = SimpleNamespace(
        IS_FIRST_RUN=False,
        STORAGE_ROOT_EXPLICITLY_SET=True,
        DEBUG=False,
        VIOLET_ENV="production",
    )
    monkeypatch.setattr(health.database, "engine", fake_engine)
    monkeypatch.setattr(health.database, "init_engine", lambda: fake_engine)
    monkeypatch.setattr(health, "settings", fake_settings)

    def fake_columns(table_name):
        columns = set(health.REQUIRED_CORE_COLUMNS[table_name])
        if table_name == "blombooru_media_tags":
            columns.remove("source")
        return [{"name": name} for name in columns]

    monkeypatch.setattr(
        health,
        "inspect",
        lambda engine: SimpleNamespace(get_table_names=lambda: list(health.REQUIRED_CORE_TABLES), get_columns=fake_columns),
    )

    import asyncio

    payload = asyncio.run(health.get_health())

    assert payload["ok"] is False
    assert payload["schema_compatible"] is False
    assert payload["schema_status"] == "missing_required_columns:blombooru_media_tags"


def test_health_route_allows_anonymous_get_when_auth_required(monkeypatch):
    async def health_route(_request):
        return JSONResponse(
            {
                "ok": False,
                "app_name": "V.I.O.L.E.T.",
                "env": "production",
                "db_reachable": True,
                "schema_compatible": False,
                "schema_status": "missing_required_tables",
                "storage_configured": True,
                "debug": False,
            }
        )

    monkeypatch.setattr(auth_middleware, "settings", SimpleNamespace(REQUIRE_AUTH=True))
    app = Starlette(routes=[Route("/api/health", health_route)])
    app.add_middleware(AuthMiddleware)
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_compatible"] is False
    assert "storage_root" not in body
    assert "password" not in json.dumps(body).lower()


def test_health_route_is_public_for_launcher_polling():
    async def app(scope, receive, send):
        response = PlainTextResponse("ok")
        await response(scope, receive, send)

    middleware = AuthMiddleware(app)

    assert middleware.is_public_route("/api/health") is True
