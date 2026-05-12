"""Tests for scripts/inspect_pilot_dataset.py — generic pilot dataset inspector."""
import json
import os
import subprocess
import sys
from pathlib import Path

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


def test_nonexistent_dir():
    r = _run(["--path", "/nonexistent/path/abc123", "--json"])
    data = json.loads(r.stdout)
    assert data["exists"] is False
    assert len(data["errors"]) > 0


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


def test_human_readable_output(pilot_dir: Path):
    r = _run(["--path", str(pilot_dir)])
    assert r.returncode == 0
    assert "Supported images:" in r.stdout
    assert "Unsupported files:" in r.stdout
    assert "Hidden/system files:" in r.stdout


def test_empty_dir(tmp_path: Path):
    r = _run(["--path", str(tmp_path), "--json"])
    data = json.loads(r.stdout)
    assert data["exists"] is True
    assert data["total_files"] == 0
    assert data["supported"] == 0
