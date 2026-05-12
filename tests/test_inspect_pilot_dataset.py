"""Tests for scripts/inspect_pilot_dataset.py — generic pilot dataset inspector."""
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "inspect_pilot_dataset.py")


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, SCRIPT] + args,
        capture_output=True, text=True, timeout=30,
    )


@pytest.fixture()
def pilot_dir(tmp_path: Path):
    """Create a temp pilot dataset with known files."""
    (tmp_path / "image1.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 100)
    (tmp_path / "image2.png").write_bytes(b"\x89PNG" + b"\x00" * 200)
    (tmp_path / "photo.webp").write_bytes(b"RIFF" + b"\x00" * 50)

    (tmp_path / "video.mp4").write_bytes(b"\x00" * 80)
    (tmp_path / "readme.txt").write_text("hello")
    (tmp_path / "raw.heic").write_bytes(b"\x00" * 60)

    (tmp_path / "desktop.ini").write_text("[.ShellClassInfo]")
    (tmp_path / ".hidden_file").write_text("hidden")

    nested = tmp_path / "subfolder" / "deep"
    nested.mkdir(parents=True)
    (nested / "nested.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 150)
    (nested / "nested.bmp").write_bytes(b"BM" + b"\x00" * 40)

    return tmp_path


# ---------- basic counts and fields ----------

def test_basic_counts(pilot_dir: Path):
    r = _run(["--path", str(pilot_dir), "--json"])
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)

    assert data["exists"] is True
    assert data["supported"] == 4  # jpg, png, webp, nested.jpg
    assert data["unsupported"] == 4  # mp4, txt, heic, nested.bmp
    assert data["hidden"] == 2  # desktop.ini, .hidden_file
    assert data["total_files"] == 8  # supported + unsupported (hidden excluded)
    assert data["total_bytes"] > 0
    assert data["stat_errors"] == 0
    assert len(data["errors"]) == 0


def test_extension_distribution(pilot_dir: Path):
    r = _run(["--path", str(pilot_dir), "--json"])
    data = json.loads(r.stdout)
    dist = data["extension_distribution"]
    assert dist[".jpg"] == 2
    assert dist[".png"] == 1
    assert dist[".webp"] == 1


def test_sample_unsupported(pilot_dir: Path):
    r = _run(["--path", str(pilot_dir), "--json"])
    data = json.loads(r.stdout)
    assert len(data["sample_unsupported"]) > 0
    exts = {Path(p).suffix.lower() for p in data["sample_unsupported"]}
    assert exts <= {".mp4", ".txt", ".heic", ".bmp"}


# ---------- exit code semantics ----------

def test_json_nonexistent_dir_returns_nonzero():
    r = _run(["--path", "/nonexistent/path/abc123", "--json"])
    assert r.returncode != 0
    data = json.loads(r.stdout)
    assert data["exists"] is False
    assert len(data["errors"]) > 0


def test_nonjson_nonexistent_dir_returns_nonzero():
    r = _run(["--path", "/nonexistent/path/abc123"])
    assert r.returncode != 0


def test_clean_dataset_exits_zero(pilot_dir: Path):
    r = _run(["--path", str(pilot_dir), "--json"])
    assert r.returncode == 0


def test_clean_dataset_nonjson_exits_zero(pilot_dir: Path):
    r = _run(["--path", str(pilot_dir)])
    assert r.returncode == 0


# ---------- source directory safety ----------

def test_source_not_modified(pilot_dir: Path):
    before = set()
    for root, dirs, files in os.walk(pilot_dir):
        for f in files:
            p = Path(root) / f
            before.add((str(p.relative_to(pilot_dir)), p.stat().st_size))

    _run(["--path", str(pilot_dir), "--json"])

    after = set()
    for root, dirs, files in os.walk(pilot_dir):
        for f in files:
            p = Path(root) / f
            after.add((str(p.relative_to(pilot_dir)), p.stat().st_size))

    assert before == after, "Inspector must not modify source directory"


# ---------- human-readable output ----------

def test_human_readable_output(pilot_dir: Path):
    r = _run(["--path", str(pilot_dir)])
    assert r.returncode == 0
    assert "Supported images:" in r.stdout
    assert "Unsupported files:" in r.stdout
    assert "Hidden/system files:" in r.stdout
    assert "Stat errors:" in r.stdout


# ---------- empty directory ----------

def test_empty_dir(tmp_path: Path):
    r = _run(["--path", str(tmp_path), "--json"])
    data = json.loads(r.stdout)
    assert data["exists"] is True
    assert data["total_files"] == 0
    assert data["supported"] == 0
    assert data["stat_errors"] == 0


# ---------- os.walk traversal error (monkeypatch) ----------

def test_walk_onerror_recorded():
    """os.walk onerror callback records traversal errors."""
    from scripts.inspect_pilot_dataset import inspect_dataset

    real_walk = os.walk

    def _patched_walk(path, **kwargs):
        onerror = kwargs.get("onerror")
        if onerror:
            onerror(PermissionError(13, "Permission denied", "/fake/locked"))
        yield from real_walk(path, **kwargs)

    with patch("scripts.inspect_pilot_dataset.os.walk", side_effect=_patched_walk):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            result = inspect_dataset(Path(td))

    assert len(result["errors"]) >= 1
    assert any("Traversal error" in e for e in result["errors"])
    assert any("PermissionError" in e for e in result["errors"])


def test_walk_error_causes_nonzero_exit(tmp_path: Path):
    """Traversal errors cause non-zero exit in JSON mode."""
    (tmp_path / "ok.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 50)

    env = os.environ.copy()
    env["_VIOLET_INJECT_WALK_ERROR"] = "1"

    wrapper = tmp_path / "_wrapper.py"
    wrapper.write_text(
        "import os, sys\n"
        "from pathlib import Path\n"
        "from unittest.mock import patch\n"
        "sys.path.insert(0, " + repr(str(Path(SCRIPT).parent.parent)) + ")\n"
        "from scripts.inspect_pilot_dataset import inspect_dataset\n"
        "import json\n"
        "real_walk = os.walk\n"
        "def pw(p, **kw):\n"
        "    cb = kw.get('onerror')\n"
        "    if cb: cb(PermissionError(13, 'denied', '/x'))\n"
        "    yield from real_walk(p, **kw)\n"
        "with patch('scripts.inspect_pilot_dataset.os.walk', side_effect=pw):\n"
        "    r = inspect_dataset(Path(sys.argv[1]))\n"
        "print(json.dumps(r, indent=2))\n"
        "sys.exit(1 if r['errors'] else 0)\n"
    )

    r = subprocess.run(
        [sys.executable, str(wrapper), str(tmp_path)],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode != 0
    data = json.loads(r.stdout)
    assert any("Traversal error" in e for e in data["errors"])


# ---------- stat failure (monkeypatch) ----------

def test_stat_failure_recorded():
    """stat() OSError is recorded, file not counted as supported/unsupported."""
    from scripts.inspect_pilot_dataset import inspect_dataset

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        (td_path / "good.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 50)
        (td_path / "bad.png").write_bytes(b"\x89PNG" + b"\x00" * 50)

        real_stat = Path.stat

        def _patched_stat(self, *args, **kwargs):
            if self.name == "bad.png" and kwargs.get("follow_symlinks", True):
                raise PermissionError(13, "Permission denied", str(self))
            return real_stat(self, *args, **kwargs)

        with patch.object(Path, "stat", _patched_stat):
            result = inspect_dataset(td_path)

    assert result["stat_errors"] == 1
    assert result["supported"] == 1  # only good.jpg
    assert result["total_files"] == 2  # both counted in total_files
    assert len(result["errors"]) >= 1
    assert any("stat failed" in e for e in result["errors"])
    assert any("bad.png" in e for e in result["errors"])


def test_stat_failure_json_nonzero_exit():
    """JSON mode exits non-zero when stat errors are present."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        (td_path / "bad.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 50)

        wrapper = Path(td) / "_stat_wrapper.py"
        wrapper.write_text(
            "import os, sys, json\n"
            "from pathlib import Path\n"
            "from unittest.mock import patch\n"
            "sys.path.insert(0, " + repr(str(Path(SCRIPT).parent.parent)) + ")\n"
            "from scripts.inspect_pilot_dataset import inspect_dataset\n"
            "real_stat = Path.stat\n"
            "def ps(self, *a, **kw):\n"
            "    if self.name == 'bad.jpg' and kw.get('follow_symlinks', True):\n"
            "        raise PermissionError(13, 'denied', str(self))\n"
            "    return real_stat(self, *a, **kw)\n"
            "with patch.object(Path, 'stat', ps):\n"
            "    r = inspect_dataset(Path(sys.argv[1]))\n"
            "print(json.dumps(r, indent=2))\n"
            "sys.exit(1 if r['errors'] else 0)\n"
        )

        r = subprocess.run(
            [sys.executable, str(wrapper), td],
            capture_output=True, text=True, timeout=30,
        )
        assert r.returncode != 0
        data = json.loads(r.stdout)
        assert data["stat_errors"] >= 1
