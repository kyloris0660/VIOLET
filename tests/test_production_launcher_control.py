from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from starlette.responses import PlainTextResponse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import violet_production_control as control  # noqa: E402
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
    monkeypatch.setattr(control, "_acquire_start_lock", lambda: False)

    result = control.start_production(repo_root=repo, base_env=env, state_path=tmp_path / "state.json")

    assert result.ok is False
    assert result.status == "blocked"
    assert "start_already_in_progress" in result.errors


def test_status_json_summary_is_public_safe(tmp_path, safe_backends, monkeypatch):
    repo, storage, env = _write_fake_repo(tmp_path)
    monkeypatch.setattr(
        control,
        "fetch_health",
        lambda url, timeout=2.0: {
            "ok": True,
            "env": "production",
            "debug": False,
            "db_reachable": True,
            "storage_configured": True,
        },
    )

    summary = control.diagnostic_summary(repo)
    serialized = json.dumps(summary, sort_keys=True)

    assert summary["env"] == "production"
    assert summary["health_ok"] is True
    assert str(storage) not in serialized
    assert str(repo) not in serialized


def test_health_route_is_public_for_launcher_polling():
    async def app(scope, receive, send):
        response = PlainTextResponse("ok")
        await response(scope, receive, send)

    middleware = AuthMiddleware(app)

    assert middleware.is_public_route("/api/health") is True
