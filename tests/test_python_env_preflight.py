"""Tests for scripts/check_python_env.py — Python/venv environment preflight.

Subprocess tests validate CLI behavior (exit codes, JSON output).
Direct-import tests validate inference logic (infer_venv_python,
resolve_expected_python) which cannot be tested via CLI alone because
the script determines repo root from its own file location.
"""
import ast
import importlib.util
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


def _load_module():
    """Import check_python_env as a module without executing main()."""
    spec = importlib.util.spec_from_file_location("check_python_env", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- 1. inferred venv from a fake repo root ------------------------------

def test_inferred_venv_from_fake_root(tmp_path):
    """Create a fake venv tree under tmp_path so auto-inference finds it."""
    mod = _load_module()
    if sys.platform == "win32":
        fake_python = tmp_path / "venv" / "Scripts" / "python.exe"
    else:
        fake_python = tmp_path / "venv" / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("placeholder")

    result = mod.infer_venv_python(tmp_path)
    assert result is not None, "Should have inferred venv python"
    assert os.path.normcase(result) == os.path.normcase(str(fake_python))


# ---- 2. explicit --expected-python overrides inferred ---------------------

def test_explicit_overrides_inferred(tmp_path):
    """--expected-python CLI arg takes priority over inferred venv."""
    mod = _load_module()
    if sys.platform == "win32":
        fake_python = tmp_path / "venv" / "Scripts" / "python.exe"
    else:
        fake_python = tmp_path / "venv" / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("placeholder")

    expected, source = mod.resolve_expected_python(
        cli_arg=PY, repo_root=tmp_path,
    )
    assert source == "cli"
    assert os.path.normcase(os.path.realpath(expected)) == \
        os.path.normcase(os.path.realpath(PY))


def test_env_var_overrides_inferred(tmp_path):
    """VIOLET_EXPECTED_PYTHON env var takes priority over inferred."""
    mod = _load_module()
    if sys.platform == "win32":
        fake_python = tmp_path / "venv" / "Scripts" / "python.exe"
    else:
        fake_python = tmp_path / "venv" / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("placeholder")

    expected, source = mod.resolve_expected_python(
        cli_arg=None, env_var=PY, repo_root=tmp_path,
    )
    assert source == "env"
    assert os.path.normcase(os.path.realpath(expected)) == \
        os.path.normcase(os.path.realpath(PY))


# ---- 3. wrong expected-python path → exit 1 ------------------------------

def test_wrong_executable_fails():
    r = _run(["--expected-python", r"C:\nonexistent\python.exe"])
    assert r.returncode == 1
    assert "FAIL" in r.stdout


# ---- 4. no inferred + no explicit → errors ------------------------------

def test_no_venv_no_explicit_has_errors(tmp_path):
    """No repo-local venv and no CLI/env override → errors list non-empty."""
    mod = _load_module()
    expected, source = mod.resolve_expected_python(
        cli_arg=None, env_var="", repo_root=tmp_path,
    )
    assert expected is None
    assert source == "none"

    result = mod.run_checks(expected_python=None, expected_code_root=None)
    assert result["pass"] is False
    assert result["expected_python"] is None
    assert isinstance(result["errors"], list)
    assert len(result["errors"]) > 0


# ---- 5. JSON output always includes "errors" field -----------------------

def test_json_success_has_errors_field():
    r = _run(["--expected-python", PY, "--json"])
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["pass"] is True
    assert "errors" in data
    assert isinstance(data["errors"], list)
    assert len(data["errors"]) == 0


def test_json_failure_has_errors_field():
    r = _run(["--expected-python", r"C:\nonexistent\python.exe", "--json"])
    assert r.returncode == 1
    data = json.loads(r.stdout)
    assert data["pass"] is False
    assert "errors" in data
    assert isinstance(data["errors"], list)
    assert len(data["errors"]) > 0


# ---- 6. correct sys.executable → exit 0 ----------------------------------

def test_correct_executable_passes():
    r = _run(["--expected-python", PY])
    assert r.returncode == 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"
    assert "PASS" in r.stdout


# ---- 7. expected-code-root mismatch → exit 1 -----------------------------

def test_code_root_mismatch_fails():
    r = _run([
        "--expected-python", PY,
        "--expected-code-root", r"C:\nonexistent\project",
    ])
    assert r.returncode == 1
    assert "FAIL" in r.stdout


# ---- 8. human-readable output includes sys.executable --------------------

def test_human_output_includes_executable():
    r = _run(["--expected-python", PY])
    assert PY in r.stdout or os.path.normcase(PY) in r.stdout.lower()


# ---- 9. script does not import backend/app dependencies -----------------

def test_no_backend_imports():
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


# ---- 10. script does not modify files -----------------------------------

def test_no_file_modifications(tmp_path):
    before = set(tmp_path.iterdir())
    _run(["--expected-python", PY], cwd=str(tmp_path))
    after = set(tmp_path.iterdir())
    assert before == after, f"New files created: {after - before}"
