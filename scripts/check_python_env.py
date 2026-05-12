#!/usr/bin/env python3
"""Python/venv environment preflight check.

Dependency-free script (stdlib only) that verifies the running Python
interpreter matches project expectations.  Designed as a hard gate for
agent workflows — must pass before any server start, test run, or script
execution.

Usage:
    python scripts/check_python_env.py
    python scripts/check_python_env.py --expected-python "C:\\...\\venv\\Scripts\\python.exe"
    python scripts/check_python_env.py --expected-code-root "C:\\...\\AnimeLocalBooru" --json

Exit codes:
    0  All checks pass.
    1  One or more checks failed.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_VENV_PYTHON = (
    r"C:\Users\kyloris\Documents\AnimeLocalBooru\venv\Scripts\python.exe"
)


def _normalise(p: str) -> str:
    return os.path.normcase(os.path.realpath(p))


def _pip_version() -> str:
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "-V"],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() if r.returncode == 0 else f"error: {r.stderr.strip()}"
    except Exception as exc:
        return f"unavailable: {exc}"


def _is_venv() -> bool:
    return hasattr(sys, "real_prefix") or (
        hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix
    )


def run_checks(expected_python: str, expected_code_root: str | None) -> dict:
    actual_exe = _normalise(sys.executable)
    expected_exe = _normalise(expected_python)
    python_match = actual_exe == expected_exe

    cwd = os.getcwd()
    code_root_match = None
    if expected_code_root:
        code_root_match = _normalise(cwd) == _normalise(expected_code_root)

    all_pass = python_match and (code_root_match is not False)

    return {
        "pass": all_pass,
        "sys_executable": sys.executable,
        "sys_executable_normalised": actual_exe,
        "expected_python": expected_python,
        "expected_python_normalised": expected_exe,
        "python_match": python_match,
        "python_version": sys.version,
        "is_venv": _is_venv(),
        "cwd": cwd,
        "expected_code_root": expected_code_root,
        "code_root_match": code_root_match,
        "pip_version": _pip_version(),
    }


def format_human(result: dict) -> str:
    status = "PASS" if result["pass"] else "FAIL"
    lines = [
        f"Python env preflight: {status}",
        f"  sys.executable:      {result['sys_executable']}",
        f"  expected-python:     {result['expected_python']}",
        f"  python-match:        {result['python_match']}",
        f"  python-version:      {result['python_version'].split()[0]}",
        f"  is-venv:             {result['is_venv']}",
        f"  cwd:                 {result['cwd']}",
    ]
    if result["expected_code_root"]:
        lines.append(f"  expected-code-root:  {result['expected_code_root']}")
        lines.append(f"  code-root-match:     {result['code_root_match']}")
    lines.append(f"  pip:                 {result['pip_version']}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Python/venv environment preflight check"
    )
    parser.add_argument(
        "--expected-python",
        default=DEFAULT_VENV_PYTHON,
        help="Expected sys.executable path (default: project venv Python)",
    )
    parser.add_argument(
        "--expected-code-root",
        default=None,
        help="Expected working directory / code root",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON",
    )
    args = parser.parse_args()

    result = run_checks(args.expected_python, args.expected_code_root)

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        print(format_human(result))

    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
