"""Check that a running V.I.O.L.E.T. test server matches expected identity.

Usage:
    python scripts/check_test_server_identity.py --base-url http://127.0.0.1:8011 \
        --expected-env test --expected-db blombooru_test \
        --expected-storage-root "C:\\Users\\kyloris\\VioletStorage\\medium"

    # With Python runtime identity verification:
    python scripts/check_test_server_identity.py --base-url http://127.0.0.1:8014 \
        --expected-env test --expected-db blombooru_test_medium \
        --expected-python "C:\\path\\to\\venv\\Scripts\\python.exe"

Exits 0 if all checks pass, 1 if any mismatch or connection failure.
On connection failure, attempts to report which process holds the port (Windows).
"""
import argparse
import json
import os
import platform
import subprocess
import sys
from urllib.parse import urlparse

import requests


def normalize_path(p: str) -> str:
    """Normalize a file path for comparison: resolve symlinks, lowercase on Windows.

    Used for code_root and other general path comparisons where symlink
    resolution is desirable (e.g. verifying the server serves from the
    expected directory regardless of how it was reached).
    """
    result = os.path.normpath(os.path.realpath(p))
    if platform.system() == "Windows":
        result = result.lower()
    return result


def normalize_executable_path(p: str) -> str:
    """Normalize a Python executable path for identity comparison.

    Unlike normalize_path(), this intentionally does NOT resolve symlinks.
    On POSIX, venv/bin/python is typically a symlink to the base interpreter.
    If we resolved symlinks, two distinct venvs pointing at the same base
    Python would compare equal — defeating the purpose of verifying which
    venv the server is running under.

    Normalization applied:
    - expanduser (~)
    - abspath (relative → absolute)
    - normpath (redundant separators, . / ..)
    - normcase on Windows (lowercase + backslash, since NTFS is case-insensitive)
    """
    expanded = os.path.expanduser(p)
    absolute = os.path.abspath(expanded)
    normalized = os.path.normpath(absolute)
    if platform.system() == "Windows":
        normalized = os.path.normcase(normalized)
    return normalized


def detect_port_owner(port: int) -> str:
    if platform.system() != "Windows":
        return ""
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"], text=True, stderr=subprocess.DEVNULL
        )
        for line in out.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                pid = parts[-1]
                try:
                    tasklist = subprocess.check_output(
                        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                        text=True, stderr=subprocess.DEVNULL,
                    ).strip()
                    return f"PID {pid} -> {tasklist}"
                except Exception:
                    return f"PID {pid}"
        return "no LISTENING process found"
    except Exception as e:
        return f"(netstat failed: {e})"


def main():
    parser = argparse.ArgumentParser(description="Verify V.I.O.L.E.T. test server identity")
    parser.add_argument("--base-url", default="http://127.0.0.1:8011")
    parser.add_argument("--expected-env", default=None)
    parser.add_argument("--expected-db", default=None)
    parser.add_argument("--expected-code-root", default=None)
    parser.add_argument("--expected-git-sha", default=None)
    parser.add_argument("--expected-branch", default=None)
    parser.add_argument("--admin-username", default="admin", help="Admin username for auth")
    parser.add_argument("--admin-password", default=None, help="Admin password for auth")
    parser.add_argument("--expected-storage-root", default=None,
                        help="Expected VIOLET_STORAGE_ROOT path (compared with symlink resolution)")
    parser.add_argument(
        "--expected-python", default=None,
        help="Expected Python executable path for the server runtime. "
             "Compared against server-reported python_executable with path normalization."
    )
    args = parser.parse_args()

    url = f"{args.base_url.rstrip('/')}/api/system/server-identity"
    parsed = urlparse(args.base_url)
    port = parsed.port or 80

    session = requests.Session()
    # Disable inheriting proxy env vars (HTTP_PROXY / HTTPS_PROXY).
    # Identity checks target localhost — routing them through an external
    # proxy causes spurious connection failures.
    session.trust_env = False
    if args.admin_password:
        login_url = f"{args.base_url.rstrip('/')}/api/admin/login"
        try:
            resp = session.post(
                login_url,
                json={"username": args.admin_username, "password": args.admin_password},
                timeout=5,
            )
            if resp.status_code != 200:
                print(f"FAIL: admin login returned {resp.status_code}")
                sys.exit(1)
        except Exception as e:
            print(f"FAIL: admin login failed: {e}")
            sys.exit(1)

    try:
        resp = session.get(url, timeout=5)
    except requests.ConnectionError:
        owner = detect_port_owner(port)
        print(f"FAIL: cannot connect to {args.base_url}")
        if owner:
            print(f"  Port {port} owner: {owner}")
        else:
            print(f"  Port {port}: no process detected")
        sys.exit(1)
    except Exception as e:
        print(f"FAIL: request error: {e}")
        sys.exit(1)

    if resp.status_code == 401 or resp.status_code == 403:
        print(f"FAIL: server returned {resp.status_code} — admin auth required. Use --admin-password.")
        sys.exit(1)

    if resp.status_code != 200:
        print(f"FAIL: server returned HTTP {resp.status_code}")
        print(f"  Body: {resp.text[:500]}")
        sys.exit(1)

    data = resp.json()
    print(f"Server identity: {json.dumps(data, indent=2, ensure_ascii=False)}")

    mismatches = []
    checks = [
        ("expected-env", args.expected_env, data.get("violet_env")),
        ("expected-db", args.expected_db, data.get("db_name")),
        ("expected-code-root", args.expected_code_root, data.get("code_root")),
        ("expected-git-sha", args.expected_git_sha, data.get("git_sha")),
        ("expected-branch", args.expected_branch, data.get("git_branch")),
    ]

    for label, expected, actual in checks:
        if expected is None:
            continue
        if expected != actual:
            mismatches.append(f"  {label}: expected={expected!r}, actual={actual!r}")

    # Python executable path comparison uses lexical normalization only —
    # NO symlink resolution.  On POSIX, venv/bin/python is a symlink to
    # the base interpreter; resolving it would make distinct venvs compare
    # equal, hiding a real mismatch.  See normalize_executable_path().
    if args.expected_python is not None:
        actual_python = data.get("python_executable", "")
        if not actual_python:
            mismatches.append(
                "  expected-python: server did not report python_executable "
                "(upgrade server to include Python runtime identity)"
            )
        else:
            norm_expected = normalize_executable_path(args.expected_python)
            norm_actual = normalize_executable_path(actual_python)
            if norm_expected != norm_actual:
                mismatches.append(
                    f"  expected-python: expected={args.expected_python!r} "
                    f"(normalized={norm_expected!r}), "
                    f"actual={actual_python!r} (normalized={norm_actual!r})"
                )

    # Storage root uses path normalization (resolve symlinks, lowercase on Windows)
    if args.expected_storage_root is not None:
        actual_storage = data.get("storage_root", "")
        norm_expected = normalize_path(args.expected_storage_root)
        norm_actual = normalize_path(actual_storage)
        if norm_expected != norm_actual:
            mismatches.append(
                f"  expected-storage-root: expected={args.expected_storage_root!r} "
                f"(normalized={norm_expected!r}), actual={actual_storage!r} "
                f"(normalized={norm_actual!r})"
            )

    # Report is_venv status (informational, non-blocking unless --expected-python fails)
    is_venv = data.get("is_venv")
    if is_venv is not None:
        venv_label = "YES" if is_venv else "NO"
        print(f"\nPython runtime: executable={data.get('python_executable', 'N/A')}, "
              f"version={data.get('python_version', 'N/A')}, is_venv={venv_label}")

    if mismatches:
        print(f"\nFAIL: {len(mismatches)} mismatch(es):")
        for m in mismatches:
            print(m)
        sys.exit(1)

    print("\nOK: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
