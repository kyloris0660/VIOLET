"""Tests for scripts/stage_pilot_files.py — staging manifest validator (dry-run only)."""
import csv
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "stage_pilot_files.py"
SCRIPT = str(SCRIPT_PATH)

_spec = importlib.util.spec_from_file_location("stage_pilot_files", SCRIPT_PATH)
_module = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_module)
validate_manifest = _module.validate_manifest


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, SCRIPT] + args,
        capture_output=True, text=True, timeout=30,
    )


FIELDNAMES = [
    "row_id", "source_path", "proposed_target_path", "extension",
    "size_bytes", "selection_reason", "duplicate_key", "exclusion_reason",
    "placeholder_flag", "stat_error",
]


def _write_manifest(path: Path, rows: list[dict]):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture()
def valid_manifest(tmp_path: Path):
    """Create a valid manifest with real source files."""
    src1 = tmp_path / "src_img1.jpg"
    src2 = tmp_path / "src_img2.png"
    src1.write_bytes(b"\xff\xd8" + b"\x00" * 2000)
    src2.write_bytes(b"\x89PNG" + b"\x00" * 3000)

    target_root = tmp_path / "target"
    manifest_path = tmp_path / "manifest.csv"

    rows = [
        {
            "row_id": "1", "source_path": str(src1),
            "proposed_target_path": str(target_root / "src_img1.jpg"),
            "extension": ".jpg", "size_bytes": "2002",
            "selection_reason": "existing_tier500", "duplicate_key": "",
            "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
        },
        {
            "row_id": "2", "source_path": str(src2),
            "proposed_target_path": str(target_root / "src_img2.png"),
            "extension": ".png", "size_bytes": "3004",
            "selection_reason": "new_candidate", "duplicate_key": "",
            "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
        },
    ]
    _write_manifest(manifest_path, rows)
    return manifest_path, target_root


# ---------------------------------------------------------------------------
# 1. Valid manifest passes
# ---------------------------------------------------------------------------

class TestValidManifest:
    def test_valid_manifest_passes(self, valid_manifest):
        manifest_path, target_root = valid_manifest
        result = validate_manifest(manifest_path, target_root)
        assert result["valid"] is True
        assert result["existing_tier500_rows"] == 1
        assert result["new_candidate_rows"] == 1
        assert len(result["errors"]) == 0

    def test_cli_dry_run_exits_zero(self, valid_manifest):
        manifest_path, target_root = valid_manifest
        r = _run(["--manifest", str(manifest_path), "--target-root", str(target_root), "--dry-run"])
        assert r.returncode == 0
        assert "[DRY-RUN]" in r.stdout


# ---------------------------------------------------------------------------
# 2. Missing source files produce warnings
# ---------------------------------------------------------------------------

class TestMissingSourceFiles:
    def test_missing_source_warns(self, tmp_path: Path):
        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        rows = [{
            "row_id": "1", "source_path": str(tmp_path / "nonexistent.jpg"),
            "proposed_target_path": str(target_root / "nonexistent.jpg"),
            "extension": ".jpg", "size_bytes": "5000",
            "selection_reason": "new_candidate", "duplicate_key": "",
            "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["source_files_missing"] == 1
        assert result["valid"] is True  # missing source is a warning, not error
        assert any("not found" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# 3. Filename collisions produce errors
# ---------------------------------------------------------------------------

class TestFilenameCollisions:
    def test_collision_invalidates(self, tmp_path: Path):
        src1 = tmp_path / "a.jpg"
        src2 = tmp_path / "b.jpg"
        src1.write_bytes(b"\xff\xd8" + b"\x00" * 2000)
        src2.write_bytes(b"\xff\xd8" + b"\x00" * 3000)

        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        rows = [
            {
                "row_id": "1", "source_path": str(src1),
                "proposed_target_path": str(target_root / "same_name.jpg"),
                "extension": ".jpg", "size_bytes": "2002",
                "selection_reason": "new_candidate", "duplicate_key": "",
                "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
            },
            {
                "row_id": "2", "source_path": str(src2),
                "proposed_target_path": str(target_root / "same_name.jpg"),
                "extension": ".jpg", "size_bytes": "3002",
                "selection_reason": "new_candidate", "duplicate_key": "",
                "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
            },
        ]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["valid"] is False
        assert result["target_filename_collisions"] == 1
        assert any("collision" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# 4. Unsupported extensions produce errors
# ---------------------------------------------------------------------------

class TestUnsupportedExtensions:
    def test_unsupported_ext_invalidates(self, tmp_path: Path):
        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        rows = [{
            "row_id": "1", "source_path": str(tmp_path / "video.mp4"),
            "proposed_target_path": str(target_root / "video.mp4"),
            "extension": ".mp4", "size_bytes": "50000",
            "selection_reason": "new_candidate", "duplicate_key": "",
            "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["valid"] is False
        assert result["unsupported_extensions"] == 1
        assert any("unsupported" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# 5. Empty manifest warns
# ---------------------------------------------------------------------------

class TestEmptyManifest:
    def test_empty_manifest_warns(self, tmp_path: Path):
        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        _write_manifest(manifest_path, [])

        result = validate_manifest(manifest_path, target_root)
        assert result["valid"] is True
        assert result["total_rows"] == 0
        assert any("No files to copy" in w or "empty" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# 6. --execute is blocked
# ---------------------------------------------------------------------------

class TestExecuteBlocked:
    def test_execute_flag_exits_with_error(self, valid_manifest):
        manifest_path, target_root = valid_manifest
        r = _run(["--manifest", str(manifest_path), "--target-root", str(target_root), "--execute"])
        assert r.returncode == 2
        assert "not implemented" in r.stderr.lower() or "Phase 3.3b" in r.stderr

    def test_no_dry_run_flag_exits_with_error(self, valid_manifest):
        manifest_path, target_root = valid_manifest
        r = _run(["--manifest", str(manifest_path), "--target-root", str(target_root)])
        assert r.returncode == 2
        assert "--dry-run" in r.stderr


# ---------------------------------------------------------------------------
# 7. Missing manifest file errors
# ---------------------------------------------------------------------------

class TestMissingManifestFile:
    def test_missing_manifest_errors(self, tmp_path: Path):
        target_root = tmp_path / "target"
        manifest_path = tmp_path / "nonexistent.csv"

        result = validate_manifest(manifest_path, target_root)
        assert result["valid"] is False
        assert any("not found" in e for e in result["errors"])

    def test_cli_missing_manifest_exits_nonzero(self, tmp_path: Path):
        target_root = tmp_path / "target"
        r = _run(["--manifest", str(tmp_path / "nope.csv"), "--target-root", str(target_root), "--dry-run"])
        assert r.returncode == 1


# ---------------------------------------------------------------------------
# 8. Regression: target-root escape rejection (Codex P1)
# ---------------------------------------------------------------------------

class TestTargetRootEscape:
    def test_dotdot_traversal_invalidates(self, tmp_path: Path):
        src = tmp_path / "src.jpg"
        src.write_bytes(b"\xff\xd8" + b"\x00" * 2000)

        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        escaped_path = str(target_root / ".." / "evil.jpg")
        rows = [{
            "row_id": "1", "source_path": str(src),
            "proposed_target_path": escaped_path,
            "extension": ".jpg", "size_bytes": "2002",
            "selection_reason": "new_candidate", "duplicate_key": "",
            "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["valid"] is False
        assert result["target_root_escapes"] == 1
        assert any("escape" in e for e in result["errors"])

    def test_absolute_outside_path_invalidates(self, tmp_path: Path):
        src = tmp_path / "src.jpg"
        src.write_bytes(b"\xff\xd8" + b"\x00" * 2000)

        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        rows = [{
            "row_id": "1", "source_path": str(src),
            "proposed_target_path": str(tmp_path / "elsewhere" / "bad.jpg"),
            "extension": ".jpg", "size_bytes": "2002",
            "selection_reason": "new_candidate", "duplicate_key": "",
            "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["valid"] is False
        assert result["target_root_escapes"] == 1

    def test_valid_target_inside_root_passes(self, tmp_path: Path):
        src = tmp_path / "src.jpg"
        src.write_bytes(b"\xff\xd8" + b"\x00" * 2000)

        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        rows = [{
            "row_id": "1", "source_path": str(src),
            "proposed_target_path": str(target_root / "good.jpg"),
            "extension": ".jpg", "size_bytes": "2002",
            "selection_reason": "new_candidate", "duplicate_key": "",
            "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["target_root_escapes"] == 0
        assert result["valid"] is True

    def test_empty_target_path_counted_as_escape(self, tmp_path: Path):
        src = tmp_path / "src.jpg"
        src.write_bytes(b"\xff\xd8" + b"\x00" * 2000)

        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        rows = [{
            "row_id": "1", "source_path": str(src),
            "proposed_target_path": "   ",
            "extension": ".jpg", "size_bytes": "2002",
            "selection_reason": "new_candidate", "duplicate_key": "",
            "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["target_root_escapes"] == 1
        assert result["valid"] is False
