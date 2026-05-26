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


def _listeners(mapping):
    return audit.ListenerBackendResult(
        listeners=mapping,
        backend="windows_netstat",
        status="ok",
    )


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
    monkeypatch.setattr(audit, "get_tcp_listeners", lambda ports: _listeners({}))

    code = audit.main(["--ports", "8012", "--json"])

    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["tool"] == "audit_active_violet_servers"
    assert report["read_only"] is True
    assert report["listener_backend"] == "windows_netstat"
    assert report["listener_backend_status"] == "ok"
    assert report["listener_detection_reliable"] is True
    assert report["occupied_count"] == 0
    assert report["violet_server_count"] == 0
    assert report["ports"][0]["port"] == 8012
    assert report["ports"][0]["listening"] is False


def test_unsupported_listener_backend_reports_unknown_not_false_free(monkeypatch, capsys):
    monkeypatch.setattr(audit.platform, "system", lambda: "Linux")

    code = audit.main(["--ports", "8012", "--json"])

    assert code == 0
    report = json.loads(capsys.readouterr().out)
    item = report["ports"][0]
    assert report["listener_backend"] == "unsupported_non_windows"
    assert report["listener_backend_status"] == "unsupported"
    assert "listener_backend_unsupported" in report["listener_backend_error"]
    assert report["listener_detection_reliable"] is False
    assert report["occupied_count"] is None
    assert report["violet_server_count"] is None
    assert item["listening"] is None
    assert item["server_classification"] == "listener_backend_unavailable"


def test_missing_listener_backend_does_not_crash_or_report_free(monkeypatch, capsys):
    def missing_netstat(*args, **kwargs):
        raise FileNotFoundError("netstat missing")

    monkeypatch.setattr(audit.platform, "system", lambda: "Windows")
    monkeypatch.setattr(audit, "run_command", missing_netstat)

    code = audit.main(["--ports", "8012", "--json"])

    assert code == 0
    report = json.loads(capsys.readouterr().out)
    item = report["ports"][0]
    assert report["listener_backend"] == "windows_netstat"
    assert report["listener_backend_status"] == "unavailable"
    assert "FileNotFoundError" in report["listener_backend_error"]
    assert report["listener_detection_reliable"] is False
    assert item["listening"] is None
    assert item["server_classification"] == "listener_backend_unavailable"


def test_fail_gates_fail_closed_when_listener_backend_unavailable(monkeypatch, capsys):
    monkeypatch.setattr(
        audit,
        "get_tcp_listeners",
        lambda ports: audit.ListenerBackendResult(
            listeners={},
            backend="windows_netstat",
            status="unavailable",
            error="FileNotFoundError: netstat missing",
        ),
    )

    assert audit.main(["--ports", "8012", "--fail-if-any", "--json"]) == 2
    capsys.readouterr()
    assert audit.main(["--ports", "8012", "--fail-if-stale", "--json"]) == 2


def test_process_tree_and_stale_classification_for_orphan_reloader(monkeypatch):
    monkeypatch.setattr(audit, "get_tcp_listeners", lambda ports: _listeners({8012: 39504}))
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
    assert item["server_classification"] == "confirmed_violet"
    assert item["is_confirmed_violet"] is True
    assert item["is_suspected_violet"] is False
    assert "orphan_or_reloader_mismatch" in item["stale_reasons"]
    assert "identity_pid_differs_from_listener_pid" in item["stale_reasons"]
    assert item["safe_to_stop_recommendation"] is True
    assert item["candidate_stop_pids"] == [10292]


def test_fail_if_any_returns_nonzero_for_identity_confirmed_violet(monkeypatch, capsys):
    monkeypatch.setattr(audit, "get_tcp_listeners", lambda ports: _listeners({8012: 10292}))
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
    assert report["confirmed_violet_count"] == 1
    assert report["suspected_violet_count"] == 0


def test_unauthorized_identity_with_repo_process_evidence_is_suspected_violet(monkeypatch, capsys):
    monkeypatch.setattr(audit, "get_tcp_listeners", lambda ports: _listeners({8012: 10292}))
    monkeypatch.setattr(
        audit,
        "list_processes",
        lambda: {
            10292: audit.ProcessInfo(
                pid=10292,
                parent_pid=39504,
                command_line=r"C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe run.py --debug",
                executable_path=r"C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe",
            )
        },
    )
    monkeypatch.setattr(audit, "fetch_identity", lambda *a, **k: (None, "unauthorized"))

    code = audit.main(
        [
            "--ports",
            "8012",
            "--expected-code-root",
            r"C:\Users\kyloris\Documents\AnimeLocalBooru",
            "--fail-if-any",
            "--json",
        ]
    )

    assert code == 1
    report = json.loads(capsys.readouterr().out)
    item = report["ports"][0]
    assert report["violet_server_count"] == 1
    assert report["confirmed_violet_count"] == 0
    assert report["suspected_violet_count"] == 1
    assert item["server_classification"] == "suspected_violet"
    assert item["is_violet_server"] is True
    assert item["is_confirmed_violet"] is False
    assert item["is_suspected_violet"] is True
    assert item["identity_status"] == "unauthorized"
    assert "suspected" in item["server_classification"]
    assert "expected_code_root" in item["detection_sources"]
    assert "process_command_line" in item["detection_sources"]


def test_identity_unavailable_unrelated_service_is_unknown_not_violet(monkeypatch):
    monkeypatch.setattr(audit, "get_tcp_listeners", lambda ports: _listeners({8012: 10292}))
    monkeypatch.setattr(
        audit,
        "list_processes",
        lambda: {
            10292: audit.ProcessInfo(
                pid=10292,
                parent_pid=100,
                command_line="node unrelated-service.js",
            )
        },
    )
    monkeypatch.setattr(audit, "fetch_identity", lambda *a, **k: (None, "connection_failed"))

    report = audit.build_report(_args("--ports", "8012"))
    item = report["ports"][0]

    assert item["identity_status"] == "connection_failed"
    assert item["identity_error"] == "connection_failed"
    assert item["server_classification"] == "unknown_listener"
    assert item["is_violet_server"] is False
    assert item["safe_to_stop_recommendation"] is False
    assert report["violet_server_count"] == 0
    assert report["unknown_listener_count"] == 1


def test_fail_if_stale_ignores_unrelated_identity_unavailable_listener(monkeypatch, capsys):
    monkeypatch.setattr(audit, "get_tcp_listeners", lambda ports: _listeners({8012: 9911}))
    monkeypatch.setattr(
        audit,
        "list_processes",
        lambda: {
            9911: audit.ProcessInfo(
                pid=9911,
                parent_pid=1,
                command_line="node unrelated-service.js",
            )
        },
    )
    monkeypatch.setattr(audit, "fetch_identity", lambda *a, **k: (None, "connection_failed"))

    code = audit.main(["--ports", "8012", "--fail-if-stale", "--json"])

    assert code == 0
    report = json.loads(capsys.readouterr().out)
    item = report["ports"][0]
    assert report["occupied_count"] == 1
    assert report["violet_server_count"] == 0
    assert report["stale_server_count"] == 0
    assert report["unknown_listener_count"] == 1
    assert item["server_classification"] == "unknown_listener"
    assert item["is_violet_server"] is False


def test_fail_if_stale_returns_nonzero_for_expected_identity_mismatch(monkeypatch, capsys):
    monkeypatch.setattr(audit, "get_tcp_listeners", lambda ports: _listeners({8012: 10292}))
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
    assert report["confirmed_violet_count"] == 1
    assert "expected_env" in report["ports"][0]["stale_reasons"]


def test_windows_path_normalization_accepts_equivalent_expected_paths(monkeypatch):
    monkeypatch.setattr(audit, "get_tcp_listeners", lambda ports: _listeners({8012: 10292}))
    monkeypatch.setattr(
        audit,
        "list_processes",
        lambda: {
            10292: audit.ProcessInfo(
                pid=10292,
                parent_pid=100,
                command_line="python run.py --debug",
            )
        },
    )
    monkeypatch.setattr(audit, "fetch_identity", lambda *a, **k: (_identity(pid=10292), None))

    report = audit.build_report(
        _args(
            "--ports",
            "8012",
            "--expected-code-root",
            "c:/users/kyloris/documents/animelocalbooru/",
            "--expected-storage-root",
            "c:/users/kyloris/violetstorage/test/",
        )
    )

    assert report["stale_server_count"] == 0
    assert "expected_code_root" not in report["ports"][0]["stale_reasons"]
    assert "expected_storage_root" not in report["ports"][0]["stale_reasons"]


def test_windows_path_normalization_rejects_different_paths(monkeypatch, capsys):
    monkeypatch.setattr(audit, "get_tcp_listeners", lambda ports: _listeners({8012: 10292}))
    monkeypatch.setattr(
        audit,
        "list_processes",
        lambda: {
            10292: audit.ProcessInfo(
                pid=10292,
                parent_pid=100,
                command_line="python run.py --debug",
            )
        },
    )
    monkeypatch.setattr(audit, "fetch_identity", lambda *a, **k: (_identity(pid=10292), None))

    code = audit.main(
        [
            "--ports",
            "8012",
            "--expected-code-root",
            r"D:\Other\AnimeLocalBooru",
            "--expected-storage-root",
            r"D:\Other\VioletStorage\test",
            "--fail-if-stale",
            "--json",
        ]
    )

    assert code == 1
    report = json.loads(capsys.readouterr().out)
    reasons = report["ports"][0]["stale_reasons"]
    assert "expected_code_root" in reasons
    assert "expected_storage_root" in reasons


def test_confirmed_test_server_counts_as_stale_and_recommends_reviewable_stop(monkeypatch):
    monkeypatch.setattr(audit, "get_tcp_listeners", lambda ports: _listeners({8012: 39504}))
    monkeypatch.setattr(
        audit,
        "list_processes",
        lambda: {
            39504: audit.ProcessInfo(
                pid=39504,
                parent_pid=100,
                command_line=r"C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe run.py --debug",
            ),
            10292: audit.ProcessInfo(
                pid=10292,
                parent_pid=39504,
                command_line='python.exe -c "from multiprocessing.spawn import spawn_main"',
            ),
        },
    )
    monkeypatch.setattr(audit, "fetch_identity", lambda *a, **k: (_identity(), None))

    report = audit.build_report(_args("--ports", "8012", "--include-process-tree"))
    item = report["ports"][0]

    assert item["server_classification"] == "confirmed_violet"
    assert item["identity_status"] == "ok"
    assert item["is_confirmed_violet"] is True
    assert report["stale_server_count"] == 1
    assert "identity_pid_differs_from_listener_pid" in item["stale_reasons"]
    assert item["safe_to_stop_recommendation"] is True
    assert item["candidate_stop_pids"] == [10292, 39504]


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
