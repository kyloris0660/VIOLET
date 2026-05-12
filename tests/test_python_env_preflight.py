"""Tests for scripts/check_python_env.py — Python/venv environment preflight.

All tests invoke the script via subprocess so we never pollute the test
process's own environment.  The script is stdlib-only, so it must work
with any Python >= 3.10.
"""
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_python_env.py"
PY = sys.executable


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        timeout=15,
        **kwargs,
    )


# ---- 1. correct sys.executable → exit 0 --------------------------------

def test_correct_executable_passes():
    r = _run(["--expected-python", PY])
    assert r.returncode == 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"
    assert "PASS" in r.stdout


# ---- 2. wrong expected-python path → exit 1 -----------------------------

def test_wrong_executable_fails():
    r = _run(["--expected-python", r"C:\nonexistent\python.exe"])
    assert r.returncode == 1
    assert "FAIL" in r.stdout


# ---- 3. JSON failure output is parseable --------------------------------

def test_json_output_parseable_on_failure():
    r = _run(["--expected-python", r"C:\nonexistent\python.exe", "--json"])
    assert r.returncode == 1
    data = json.loads(r.stdout)
    assert data["pass"] is False
    assert data["python_match"] is False
    assert "sys_executable" in data
    assert "expected_python" in data


def test_json_output_parseable_on_success():
    r = _run(["--expected-python", PY, "--json"])
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["pass"] is True
    assert data["python_match"] is True


# ---- 4. expected-code-root mismatch → exit 1 ----------------------------

def test_code_root_mismatch_fails():
    r = _run([
        "--expected-python", PY,
        "--expected-code-root", r"C:\nonexistent\project",
    ])
    assert r.returncode == 1
    assert "FAIL" in r.stdout


# ---- 5. human-readable output includes sys.executable -------------------

def test_human_output_includes_executable():
    r = _run(["--expected-python", PY])
    assert PY in r.stdout or os.path.normcase(PY) in r.stdout.lower()


# ---- 6. script does not import backend/app dependencies ----------------

def test_no_backend_imports():
    """Parse the script's AST and verify no imports touch backend.*."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("backend"), (
                    f"Forbidden import: {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert not node.module.startswith("backend"), (
                    f"Forbidden import from: {node.module}"
                )


# ---- 7. script does not modify files -----------------------------------

def test_no_file_modifications(tmp_path):
    """Run the script in a temp dir and verify nothing was created."""
    before = set(tmp_path.iterdir())
    _run(["--expected-python", PY], cwd=str(tmp_path))
    after = set(tmp_path.iterdir())
    assert before == after, f"New files created: {after - before}"
