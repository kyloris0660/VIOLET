import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "audit_active_violet_servers.py"
spec = importlib.util.spec_from_file_location("audit_active_violet_servers", SCRIPT_PATH)
audit = importlib.util.module_from_spec(spec)
sys.modules["audit_active_violet_servers"] = audit
assert spec.loader is not None
spec.loader.exec_module(audit)


def _args(*extra):
    return audit.make_arg_parser().parse_args(list(extra))


def _identity(**overrides):
    data = {
        "app_name": "V.I.O.L.E.T.",
        "violet_env": "test",
        "db_name": "blombooru_test",
        "storage_root": r"C:\Users\kyloris\VioletStorage\test",
        "code_root": r"C:\Users\kyloris\Documents\AnimeLocalBooru",
        "git_sha": "63934cb",
        "git_branch": "main",
        "pid": 10292,
        "python_executable": r"C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe",
        "is_venv": True,
    }
    data.update(overrides)
    return data


def test_parse_ports_accepts_ranges_and_deduplicates():
    assert audit.parse_ports("8000,8012-8014,8013") == [8000, 8012, 8013, 8014]


def test_parse_ports_rejects_descending_range():
    try:
        audit.parse_ports("8024-8012")
    except ValueError as exc:
        assert "descending" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_redacts_admin_password_from_command_line():
    command = 'python scripts/audit_active_violet_servers.py --admin-password "secret value" --json'
    redacted = audit.redact_command_line(command)
    assert "secret value" not in redacted
    assert "--admin-password" in redacted
    assert "<redacted>" in redacted


def test_json_output_shape_for_no_active_servers(monkeypatch, capsys):
    monkeypatch.setattr(audit, "get_tcp_listeners", lambda ports: {})

    code = audit.main(["--ports", "8012", "--json"])

    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["tool"] == "audit_active_violet_servers"
    assert report["read_only"] is True
    assert report["occupied_count"] == 0
    assert report["violet_server_count"] == 0
    assert report["ports"][0]["port"] == 8012
    assert report["ports"][0]["listening"] is False


def test_process_tree_and_stale_classification_for_orphan_reloader(monkeypatch):
    monkeypatch.setattr(audit, "get_tcp_listeners", lambda ports: {8012: 39504})
    monkeypatch.setattr(
        audit,
        "list_processes",
        lambda: {
            10292: audit.ProcessInfo(
                pid=10292,
                parent_pid=39504,
                command_line='python.exe -c "from multiprocessing.spawn import spawn_main"',
            )
        },
    )
    monkeypatch.setattr(audit, "fetch_identity", lambda *a, **k: (_identity(), None))

    report = audit.build_report(_args("--ports", "8012", "--include-process-tree"))
    item = report["ports"][0]

    assert item["listening"] is True
    assert item["tcp_listener_pid"] == 39504
    assert item["process_exists"] is False
    assert item["child_processes"][0]["pid"] == 10292
    assert "orphan_or_reloader_mismatch" in item["stale_reasons"]
    assert "identity_pid_differs_from_listener_pid" in item["stale_reasons"]
    assert item["safe_to_stop_recommendation"] is True
    assert item["candidate_stop_pids"] == [10292]


def test_fail_if_any_returns_nonzero_for_identity_confirmed_violet(monkeypatch, capsys):
    monkeypatch.setattr(audit, "get_tcp_listeners", lambda ports: {8012: 10292})
    monkeypatch.setattr(
        audit,
        "list_processes",
        lambda: {
            10292: audit.ProcessInfo(
                pid=10292,
                parent_pid=39504,
                command_line="python run.py --debug",
            )
        },
    )
    monkeypatch.setattr(audit, "fetch_identity", lambda *a, **k: (_identity(pid=10292), None))

    code = audit.main(["--ports", "8012", "--fail-if-any", "--json"])

    assert code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["violet_server_count"] == 1


def test_identity_unavailable_is_reported_without_success(monkeypatch):
    monkeypatch.setattr(audit, "get_tcp_listeners", lambda ports: {8012: 10292})
    monkeypatch.setattr(
        audit,
        "list_processes",
        lambda: {
            10292: audit.ProcessInfo(
                pid=10292,
                parent_pid=39504,
                command_line="python run.py --debug",
            )
        },
    )
    monkeypatch.setattr(audit, "fetch_identity", lambda *a, **k: (None, "auth_required"))

    report = audit.build_report(_args("--ports", "8012"))
    item = report["ports"][0]

    assert item["identity_status"] == "identity_unavailable"
    assert item["identity_error"] == "auth_required"
    assert item["is_violet_server"] is False
    assert item["safe_to_stop_recommendation"] is False


def test_fail_if_stale_returns_nonzero_for_expected_identity_mismatch(monkeypatch, capsys):
    monkeypatch.setattr(audit, "get_tcp_listeners", lambda ports: {8012: 10292})
    monkeypatch.setattr(
        audit,
        "list_processes",
        lambda: {
            10292: audit.ProcessInfo(
                pid=10292,
                parent_pid=39504,
                command_line="python run.py --debug",
            )
        },
    )
    monkeypatch.setattr(audit, "fetch_identity", lambda *a, **k: (_identity(pid=10292), None))

    code = audit.main(
        [
            "--ports",
            "8012",
            "--expected-env",
            "development",
            "--fail-if-stale",
            "--json",
        ]
    )

    assert code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["stale_server_count"] == 1
    assert "expected_env" in report["ports"][0]["stale_reasons"]


def test_source_has_no_stop_or_kill_path():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden_tokens = [
        "Stop-Process",
        "TerminateProcess",
        "os.kill",
        ".kill(",
        ".terminate(",
    ]
    for token in forbidden_tokens:
        assert token not in source
