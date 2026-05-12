#!/usr/bin/env python3
"""Python/venv environment preflight check.

Dependency-free script (stdlib only) that verifies the running Python
interpreter matches project expectations.  Designed as a hard gate for
agent workflows — must pass before any server start, test run, or script
execution.

Resolution order for expected Python:
    1. --expected-python CLI argument   (highest priority)
    2. VIOLET_EXPECTED_PYTHON env var
    3. Auto-inferred repo-local venv    (lowest priority)

Auto-inference probes these candidates relative to the repo root
(determined from this script's location):
    <repo>/venv/Scripts/python.exe      (Windows)
    <repo>/.venv/Scripts/python.exe     (Windows)
    <repo>/venv/bin/python              (POSIX)
    <repo>/.venv/bin/python             (POSIX)

Usage:
    python scripts/check_python_env.py
    python scripts/check_python_env.py --expected-python "/path/to/venv/bin/python"
    python scripts/check_python_env.py --expected-code-root "/path/to/repo" --json

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

_REPO_ROOT = Path(__file__).resolve().parents[1]

_VENV_CANDIDATES = [
    Path("venv", "Scripts", "python.exe"),
    Path(".venv", "Scripts", "python.exe"),
    Path("venv", "bin", "python"),
    Path(".venv", "bin", "python"),
]


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


def infer_venv_python(repo_root: Path | None = None) -> str | None:
    """Return the first existing venv Python candidate under *repo_root*."""
    root = repo_root or _REPO_ROOT
    for candidate in _VENV_CANDIDATES:
        full = root / candidate
        if full.is_file():
            return str(full)
    return None


def resolve_expected_python(
    cli_arg: str | None,
    env_var: str | None = None,
    repo_root: Path | None = None,
) -> tuple[str | None, str]:
    """Return (expected_python, source) using the resolution order."""
    if cli_arg is not None:
        return cli_arg, "cli"
    ev = env_var or os.environ.get("VIOLET_EXPECTED_PYTHON")
    if ev:
        return ev, "env"
    inferred = infer_venv_python(repo_root)
    if inferred:
        return inferred, "inferred"
    return None, "none"


def run_checks(expected_python: str | None, expected_code_root: str | None) -> dict:
    errors: list[str] = []

    if expected_python is None:
        errors.append(
            "No expected Python found. No repo-local venv detected under "
            f"{_REPO_ROOT} and --expected-python / VIOLET_EXPECTED_PYTHON not set."
        )
        return {
            "pass": False,
            "sys_executable": sys.executable,
            "sys_executable_normalised": _normalise(sys.executable),
            "expected_python": None,
            "expected_python_normalised": None,
            "python_match": False,
            "python_version": sys.version,
            "is_venv": _is_venv(),
            "cwd": os.getcwd(),
            "expected_code_root": expected_code_root,
            "code_root_match": None,
            "pip_version": _pip_version(),
            "errors": errors,
        }

    actual_exe = _normalise(sys.executable)
    expected_exe = _normalise(expected_python)
    python_match = actual_exe == expected_exe

    if not python_match:
        errors.append(
            f"Python mismatch: running {sys.executable} but expected {expected_python}"
        )

    cwd = os.getcwd()
    code_root_match = None
    if expected_code_root:
        code_root_match = _normalise(cwd) == _normalise(expected_code_root)
        if not code_root_match:
            errors.append(
                f"Code root mismatch: cwd={cwd} but expected {expected_code_root}"
            )

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
        "errors": errors,
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
    if result.get("errors"):
        lines.append("")
        for err in result["errors"]:
            lines.append(f"  ERROR: {err}")
    if result["expected_python"] is None:
        lines.append("")
        lines.append(
            "  No repo-local venv found. Pass --expected-python explicitly "
            "or set VIOLET_EXPECTED_PYTHON."
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Python/venv environment preflight check"
    )
    parser.add_argument(
        "--expected-python",
        default=None,
        help=(
            "Expected sys.executable path. If omitted, falls back to "
            "VIOLET_EXPECTED_PYTHON env var, then auto-inferred repo-local venv."
        ),
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

    expected, _source = resolve_expected_python(args.expected_python)
    result = run_checks(expected, args.expected_code_root)

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        print(format_human(result))

    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
