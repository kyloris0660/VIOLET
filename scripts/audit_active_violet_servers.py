"""Audit active local V.I.O.L.E.T. servers without stopping anything.

The tool is intended as a preflight and cleanup verifier for local validation
workflows. It inspects candidate ports, visible process metadata, and the
server identity endpoint when available. It never starts, stops, or mutates a
server.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

import requests


DEFAULT_PORTS = "8000,8012-8024"
PROCESS_MATCH_RE = re.compile(
    r"(run\.py|uvicorn|violet|animelocalbooru|multiprocessing\.spawn|python)",
    re.IGNORECASE,
)
SENSITIVE_ARG_RE = re.compile(
    r"(--admin-password(?:=|\s+))([^\s\"']+|\"[^\"]*\"|'[^']*')",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    parent_pid: int | None = None
    command_line: str = ""
    executable_path: str = ""
    creation_date: str | None = None


def parse_ports(spec: str) -> list[int]:
    ports: list[int] = []
    seen: set[int] = set()
    for raw_part in spec.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = [p.strip() for p in part.split("-", 1)]
            if not start_s or not end_s:
                raise ValueError(f"Invalid port range: {part!r}")
            start = int(start_s)
            end = int(end_s)
            if end < start:
                raise ValueError(f"Invalid descending port range: {part!r}")
            candidates = range(start, end + 1)
        else:
            candidates = [int(part)]

        for port in candidates:
            if port < 1 or port > 65535:
                raise ValueError(f"Port out of range: {port}")
            if port not in seen:
                ports.append(port)
                seen.add(port)
    if not ports:
        raise ValueError("No ports specified")
    return ports


def redact_command_line(command_line: str | None) -> str:
    if not command_line:
        return ""
    return SENSITIVE_ARG_RE.sub(r"\1<redacted>", command_line)


def run_command(args: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def tcp_listeners_from_netstat(output: str, ports: list[int]) -> dict[int, int]:
    wanted = set(ports)
    listeners: dict[int, int] = {}
    for line in output.splitlines():
        if "LISTEN" not in line.upper():
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        local = parts[1]
        pid_s = parts[-1]
        try:
            port = int(local.rsplit(":", 1)[1])
            pid = int(pid_s)
        except (IndexError, ValueError):
            continue
        if port in wanted and port not in listeners:
            listeners[port] = pid
    return listeners


def get_tcp_listeners(ports: list[int]) -> dict[int, int]:
    system = platform.system()
    if system == "Windows":
        result = run_command(["netstat", "-ano", "-p", "tcp"])
        return tcp_listeners_from_netstat(result.stdout, ports)

    result = run_command(["netstat", "-anp", "tcp"])
    return tcp_listeners_from_netstat(result.stdout, ports)


def process_infos_from_powershell_json(output: str) -> list[ProcessInfo]:
    text = output.strip()
    if not text:
        return []
    data = json.loads(text)
    rows = [data] if isinstance(data, dict) else data

    infos: list[ProcessInfo] = []
    for row in rows:
        try:
            pid = int(row.get("ProcessId"))
        except (TypeError, ValueError):
            continue
        parent_raw = row.get("ParentProcessId")
        try:
            parent_pid = int(parent_raw) if parent_raw is not None else None
        except (TypeError, ValueError):
            parent_pid = None
        infos.append(
            ProcessInfo(
                pid=pid,
                parent_pid=parent_pid,
                command_line=redact_command_line(row.get("CommandLine") or ""),
                executable_path=redact_command_line(row.get("ExecutablePath") or ""),
                creation_date=row.get("CreationDate"),
            )
        )
    return infos


def list_processes() -> dict[int, ProcessInfo]:
    if platform.system() == "Windows":
        command = (
            "Get-CimInstance Win32_Process | "
            "Select-Object ProcessId,ParentProcessId,CommandLine,ExecutablePath,CreationDate | "
            "ConvertTo-Json -Depth 3"
        )
        result = run_command(
            ["powershell", "-NoProfile", "-Command", command],
            timeout=10,
        )
        if result.returncode != 0:
            return {}
        return {p.pid: p for p in process_infos_from_powershell_json(result.stdout)}

    result = run_command(["ps", "-eo", "pid=,ppid=,command="], timeout=10)
    infos: dict[int, ProcessInfo] = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
            parent_pid = int(parts[1])
        except ValueError:
            continue
        infos[pid] = ProcessInfo(
            pid=pid,
            parent_pid=parent_pid,
            command_line=redact_command_line(parts[2] if len(parts) > 2 else ""),
            executable_path="",
        )
    return infos


def collect_descendants(
    processes: dict[int, ProcessInfo], root_pid: int, *, matching_only: bool
) -> list[ProcessInfo]:
    descendants: list[ProcessInfo] = []
    queue = [root_pid]
    visited: set[int] = set()
    while queue:
        parent = queue.pop(0)
        if parent in visited:
            continue
        visited.add(parent)
        children = [p for p in processes.values() if p.parent_pid == parent]
        for child in children:
            queue.append(child.pid)
            if (not matching_only) or PROCESS_MATCH_RE.search(child.command_line):
                descendants.append(child)
    return descendants


def likely_violet_identity(identity: dict[str, Any] | None) -> bool:
    if not identity:
        return False
    app_name = str(identity.get("app_name") or "")
    code_root = str(identity.get("code_root") or "")
    return "V.I.O.L.E.T." in app_name or "AnimeLocalBooru" in code_root


def normalize_for_match(value: str | None) -> str:
    if not value:
        return ""
    normalized = os.path.normpath(value)
    if platform.system() == "Windows":
        normalized = normalized.lower()
    return normalized


def process_texts(*process_groups: ProcessInfo | list[ProcessInfo] | None) -> list[str]:
    texts: list[str] = []
    for group in process_groups:
        if group is None:
            continue
        processes = group if isinstance(group, list) else [group]
        for proc in processes:
            if proc.command_line:
                texts.append(proc.command_line)
            if proc.executable_path:
                texts.append(proc.executable_path)
    return texts


def detect_process_sources(
    *,
    listener_process: ProcessInfo | None,
    child_processes: list[ProcessInfo],
    args: argparse.Namespace,
    identity_error: str | None,
) -> list[str]:
    texts = process_texts(listener_process, child_processes)
    lower_texts = [t.lower() for t in texts]
    blob = "\n".join(lower_texts)
    sources: set[str] = set()

    expected_root = normalize_for_match(args.expected_code_root)
    if expected_root:
        normalized_blob = "\n".join(normalize_for_match(t) for t in texts)
        if expected_root in normalized_blob:
            sources.add("expected_code_root")
        venv_markers = [
            os.path.join(expected_root, "venv").lower(),
            os.path.join(expected_root, ".venv").lower(),
        ]
        if any(marker in normalized_blob for marker in venv_markers):
            sources.add("repo_venv")

    repo_name_evidence = any(
        token in blob
        for token in ("v.i.o.l.e.t", "animelocalbooru", "violet")
    )
    if repo_name_evidence:
        sources.add("process_command_line")

    auth_endpoint_seen = identity_error in {"unauthorized", "forbidden"}
    run_py_seen = "run.py" in blob
    backend_seen = "backend.app.main" in blob
    uvicorn_seen = "uvicorn" in blob
    if run_py_seen and (
        repo_name_evidence
        or "expected_code_root" in sources
        or "repo_venv" in sources
        or auth_endpoint_seen
    ):
        sources.add("process_command_line")
    if backend_seen and (uvicorn_seen or repo_name_evidence or "expected_code_root" in sources):
        sources.add("process_command_line")

    multiprocessing_child = any(
        "multiprocessing.spawn" in (child.command_line or "").lower()
        for child in child_processes
    )
    if multiprocessing_child and (
        "process_command_line" in sources
        or "expected_code_root" in sources
        or "repo_venv" in sources
        or auth_endpoint_seen
    ):
        sources.add("process_tree")

    return sorted(sources) or ["none"]


def identity_status(identity: dict[str, Any] | None, identity_error: str | None) -> str:
    if identity:
        return "ok" if likely_violet_identity(identity) else "not_violet"
    if identity_error in {"unauthorized", "forbidden", "connection_failed"}:
        return identity_error
    if identity_error:
        return "unavailable"
    return "unavailable"


def classify_server(
    *,
    listener_pid: int | None,
    identity: dict[str, Any] | None,
    detection_sources: list[str],
) -> str:
    if listener_pid is None:
        return "not_listening"
    if likely_violet_identity(identity):
        return "confirmed_violet"
    if identity is not None:
        return "non_violet"
    if any(source != "none" for source in detection_sources):
        return "suspected_violet"
    return "unknown_listener"


def fetch_identity(
    base_url: str,
    *,
    timeout: float,
    admin_username: str | None = None,
    admin_password: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    session = requests.Session()
    session.trust_env = False
    root = base_url.rstrip("/")

    if admin_password:
        try:
            login = session.post(
                f"{root}/api/admin/login",
                json={"username": admin_username or "admin", "password": admin_password},
                timeout=timeout,
            )
        except requests.RequestException as exc:
            return None, f"admin_login_error:{exc.__class__.__name__}"
        if login.status_code != 200:
            return None, f"admin_login_http_{login.status_code}"

    try:
        response = session.get(f"{root}/api/system/server-identity", timeout=timeout)
    except requests.ConnectionError:
        return None, "connection_failed"
    except requests.Timeout:
        return None, "timeout"
    except requests.RequestException as exc:
        return None, f"request_error:{exc.__class__.__name__}"

    if response.status_code == 401:
        return None, "unauthorized"
    if response.status_code == 403:
        return None, "forbidden"
    if response.status_code != 200:
        return None, f"http_{response.status_code}"
    try:
        return response.json(), None
    except ValueError:
        return None, "invalid_json"


def expected_mismatches(identity: dict[str, Any], args: argparse.Namespace) -> list[str]:
    checks = [
        ("expected_code_root", args.expected_code_root, identity.get("code_root")),
        ("expected_branch", args.expected_branch, identity.get("git_branch")),
        ("expected_env", args.expected_env, identity.get("violet_env")),
        ("expected_db", args.expected_db, identity.get("db_name")),
        ("expected_storage_root", args.expected_storage_root, identity.get("storage_root")),
    ]
    return [
        label
        for label, expected, actual in checks
        if expected is not None and expected != actual
    ]


def classify_stale_reasons(
    *,
    listener_pid: int,
    process_exists: bool,
    child_processes: list[ProcessInfo],
    identity: dict[str, Any] | None,
    identity_error: str | None,
    mismatches: list[str],
) -> list[str]:
    reasons: list[str] = []
    if listener_pid and not process_exists and child_processes:
        reasons.append("orphan_or_reloader_mismatch")
    if listener_pid and not process_exists and not child_processes and not identity:
        reasons.append("unknown_listener")
    if mismatches:
        reasons.extend(mismatches)
    if identity and listener_pid and identity.get("pid") not in {None, listener_pid}:
        reasons.append("identity_pid_differs_from_listener_pid")
    return reasons


def process_to_dict(info: ProcessInfo | None) -> dict[str, Any] | None:
    if info is None:
        return None
    return {
        "pid": info.pid,
        "parent_pid": info.parent_pid,
        "command_line": redact_command_line(info.command_line),
        "executable_path": redact_command_line(info.executable_path),
        "creation_date": info.creation_date,
    }


def audit_port(
    port: int,
    *,
    listener_pid: int | None,
    processes: dict[int, ProcessInfo],
    args: argparse.Namespace,
) -> dict[str, Any]:
    base_url = args.base_url_template.format(port=port)
    listener_process = processes.get(listener_pid) if listener_pid else None
    child_processes = (
        collect_descendants(processes, listener_pid, matching_only=True)
        if listener_pid and args.include_process_tree
        else []
    )
    identity, identity_error = (None, None)
    if listener_pid:
        identity, identity_error = fetch_identity(
            base_url,
            timeout=args.timeout,
            admin_username=args.admin_username,
            admin_password=args.admin_password,
        )

    detection_sources = detect_process_sources(
        listener_process=listener_process,
        child_processes=child_processes,
        args=args,
        identity_error=identity_error,
    )
    server_classification = classify_server(
        listener_pid=listener_pid,
        identity=identity,
        detection_sources=detection_sources,
    )
    is_confirmed_violet = server_classification == "confirmed_violet"
    is_suspected_violet = server_classification == "suspected_violet"
    is_violet = is_confirmed_violet or is_suspected_violet

    mismatches = expected_mismatches(identity, args) if identity else []
    candidate_stale_reasons = classify_stale_reasons(
        listener_pid=listener_pid or 0,
        process_exists=listener_process is not None,
        child_processes=child_processes,
        identity=identity,
        identity_error=identity_error,
        mismatches=mismatches,
    )
    stale_reasons = candidate_stale_reasons if is_violet else []
    suspected_strong_enough_to_stop = bool(
        is_suspected_violet
        and stale_reasons
        and (
            "expected_code_root" in detection_sources
            or "repo_venv" in detection_sources
            or {"process_command_line", "process_tree"}.issubset(set(detection_sources))
        )
    )
    safe_to_stop = bool(
        (
            is_confirmed_violet
            and identity
            and (
            str(identity.get("violet_env") or "").lower() == "test"
            or bool(stale_reasons)
            )
        )
        or suspected_strong_enough_to_stop
    )
    candidate_stop_pids: list[int] = []
    if safe_to_stop:
        if listener_process is not None and listener_pid:
            candidate_stop_pids.append(listener_pid)
        identity_pid = identity.get("pid") if identity else None
        if isinstance(identity_pid, int):
            candidate_stop_pids.append(identity_pid)
        candidate_stop_pids.extend(child.pid for child in child_processes)
    candidate_stop_pids = sorted(set(candidate_stop_pids))

    return {
        "port": port,
        "base_url": base_url,
        "listening": listener_pid is not None,
        "tcp_listener_pid": listener_pid,
        "process_exists": listener_process is not None,
        "process": process_to_dict(listener_process),
        "child_processes": [process_to_dict(p) for p in child_processes],
        "identity": identity,
        "identity_status": identity_status(identity, identity_error),
        "identity_error": identity_error,
        "server_classification": server_classification,
        "detection_sources": detection_sources,
        "is_violet_server": is_violet,
        "is_confirmed_violet": is_confirmed_violet,
        "is_suspected_violet": is_suspected_violet,
        "stale_reasons": stale_reasons,
        "safe_to_stop_recommendation": safe_to_stop,
        "candidate_stop_pids": candidate_stop_pids,
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    ports = parse_ports(args.ports)
    listeners = get_tcp_listeners(ports)
    processes = list_processes() if listeners else {}
    ports_report = [
        audit_port(
            port,
            listener_pid=listeners.get(port),
            processes=processes,
            args=args,
        )
        for port in ports
    ]
    occupied = [p for p in ports_report if p["listening"]]
    violet = [p for p in occupied if p["is_violet_server"]]
    confirmed = [p for p in occupied if p["is_confirmed_violet"]]
    suspected = [p for p in occupied if p["is_suspected_violet"]]
    stale = [p for p in violet if p["stale_reasons"]]
    unknown = [p for p in occupied if p["server_classification"] == "unknown_listener"]
    unrelated = [p for p in occupied if p["server_classification"] == "non_violet"]
    return {
        "tool": "audit_active_violet_servers",
        "read_only": True,
        "ports": ports_report,
        "occupied_count": len(occupied),
        "violet_server_count": len(violet),
        "confirmed_violet_count": len(confirmed),
        "suspected_violet_count": len(suspected),
        "unknown_listener_count": len(unknown),
        "unrelated_listener_count": len(unrelated),
        "stale_server_count": len(stale),
    }


def print_text_report(report: dict[str, Any]) -> None:
    print("V.I.O.L.E.T. active server audit")
    print(f"read_only: {report['read_only']}")
    print(f"occupied_count: {report['occupied_count']}")
    print(f"violet_server_count: {report['violet_server_count']}")
    print(f"confirmed_violet_count: {report['confirmed_violet_count']}")
    print(f"suspected_violet_count: {report['suspected_violet_count']}")
    print(f"unknown_listener_count: {report['unknown_listener_count']}")
    print(f"unrelated_listener_count: {report['unrelated_listener_count']}")
    print(f"stale_server_count: {report['stale_server_count']}")
    for item in report["ports"]:
        if not item["listening"]:
            continue
        print("")
        print(f"port: {item['port']}")
        print(f"  tcp_listener_pid: {item['tcp_listener_pid']}")
        print(f"  server_classification: {item['server_classification']}")
        print(f"  detection_sources: {', '.join(item['detection_sources'])}")
        print(f"  process_exists: {item['process_exists']}")
        if item["process"]:
            print(f"  process_command_line: {item['process']['command_line']}")
            if item["process"]["executable_path"]:
                print(f"  process_executable_path: {item['process']['executable_path']}")
        print(f"  identity_status: {item['identity_status']}")
        if item["identity_error"]:
            print(f"  identity_error: {item['identity_error']}")
        identity = item["identity"] or {}
        if identity:
            print(f"  app_name: {identity.get('app_name')}")
            print(f"  violet_env: {identity.get('violet_env')}")
            print(f"  db_name: {identity.get('db_name')}")
            print(f"  storage_root: {identity.get('storage_root')}")
            print(f"  code_root: {identity.get('code_root')}")
            print(f"  git_sha: {identity.get('git_sha')}")
            print(f"  git_branch: {identity.get('git_branch')}")
            print(f"  identity_pid: {identity.get('pid')}")
            print(f"  python_executable: {identity.get('python_executable')}")
            print(f"  is_venv: {identity.get('is_venv')}")
        if item["child_processes"]:
            print("  child_processes:")
            for child in item["child_processes"]:
                print(f"    - pid={child['pid']} parent={child['parent_pid']}")
                print(f"      command_line={child['command_line']}")
        if item["stale_reasons"]:
            print(f"  stale_reasons: {', '.join(item['stale_reasons'])}")
        print(f"  safe_to_stop_recommendation: {item['safe_to_stop_recommendation']}")
        if item["candidate_stop_pids"]:
            print(f"  candidate_stop_pids: {', '.join(map(str, item['candidate_stop_pids']))}")


def make_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only audit of active local V.I.O.L.E.T. servers."
    )
    parser.add_argument("--ports", default=DEFAULT_PORTS)
    parser.add_argument("--base-url-template", default="http://127.0.0.1:{port}")
    parser.add_argument("--json", action="store_true", help="Print JSON report")
    parser.add_argument("--fail-if-any", action="store_true")
    parser.add_argument("--fail-if-stale", action="store_true")
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--include-process-tree", action="store_true")
    parser.add_argument("--expected-code-root", default=None)
    parser.add_argument("--expected-branch", default=None)
    parser.add_argument("--expected-env", default=None)
    parser.add_argument("--expected-db", default=None)
    parser.add_argument("--expected-storage-root", default=None)
    parser.add_argument("--admin-username", default="admin")
    parser.add_argument("--admin-password", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = make_arg_parser()
    args = parser.parse_args(argv)
    try:
        report = build_report(args)
    except Exception as exc:
        print(f"FAIL: audit failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_text_report(report)

    if args.fail_if_any and report["violet_server_count"]:
        return 1
    if args.fail_if_stale and report["stale_server_count"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
