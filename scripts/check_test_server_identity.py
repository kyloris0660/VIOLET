"""Check that a running V.I.O.L.E.T. test server matches expected identity.

Usage:
    python scripts/check_test_server_identity.py --base-url http://127.0.0.1:8011 \
        --expected-env test --expected-db blombooru_test

Exits 0 if all checks pass, 1 if any mismatch or connection failure.
On connection failure, attempts to report which process holds the port (Windows).
"""
import argparse
import json
import platform
import subprocess
import sys
from urllib.parse import urlparse

import requests


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
    args = parser.parse_args()

    url = f"{args.base_url.rstrip('/')}/api/system/server-identity"
    parsed = urlparse(args.base_url)
    port = parsed.port or 80

    session = requests.Session()
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

    if mismatches:
        print(f"\nFAIL: {len(mismatches)} mismatch(es):")
        for m in mismatches:
            print(m)
        sys.exit(1)

    print("\nOK: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
