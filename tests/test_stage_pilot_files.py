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
    def test_missing_source_invalidates(self, tmp_path: Path):
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
        assert result["valid"] is False
        assert any("not found" in e for e in result["errors"])


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


# ---------------------------------------------------------------------------
# 9. Blank source_path on non-excluded rows (Codex P2)
# ---------------------------------------------------------------------------

class TestBlankSourcePath:
    def test_blank_source_path_invalidates(self, tmp_path: Path):
        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        rows = [{
            "row_id": "1", "source_path": "",
            "proposed_target_path": str(target_root / "img.jpg"),
            "extension": ".jpg", "size_bytes": "5000",
            "selection_reason": "new_candidate", "duplicate_key": "",
            "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["blank_source_paths"] == 1
        assert result["valid"] is False
        assert any("blank source_path" in e for e in result["errors"])

    def test_whitespace_only_source_path_invalidates(self, tmp_path: Path):
        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        rows = [{
            "row_id": "1", "source_path": "   ",
            "proposed_target_path": str(target_root / "img.jpg"),
            "extension": ".jpg", "size_bytes": "5000",
            "selection_reason": "new_candidate", "duplicate_key": "",
            "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["blank_source_paths"] == 1
        assert result["valid"] is False

    def test_existing_tier500_blank_source_path_invalidates(self, tmp_path: Path):
        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        rows = [{
            "row_id": "1", "source_path": "",
            "proposed_target_path": str(target_root / "img.jpg"),
            "extension": ".jpg", "size_bytes": "5000",
            "selection_reason": "existing_tier500", "duplicate_key": "",
            "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["blank_source_paths"] == 1
        assert result["valid"] is False

    def test_excluded_row_blank_source_ok(self, tmp_path: Path):
        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        rows = [{
            "row_id": "1", "source_path": "",
            "proposed_target_path": "",
            "extension": ".mp4", "size_bytes": "5000",
            "selection_reason": "", "duplicate_key": "",
            "exclusion_reason": "unsupported_format:.mp4",
            "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["blank_source_paths"] == 0
        assert result["valid"] is True


# ---------------------------------------------------------------------------
# 10. Blank proposed_target_path on non-excluded rows (Codex P2)
# ---------------------------------------------------------------------------

class TestBlankTargetPath:
    def test_blank_target_path_invalidates(self, tmp_path: Path):
        src = tmp_path / "src.jpg"
        src.write_bytes(b"\xff\xd8" + b"\x00" * 2000)
        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        rows = [{
            "row_id": "1", "source_path": str(src),
            "proposed_target_path": "",
            "extension": ".jpg", "size_bytes": "2002",
            "selection_reason": "new_candidate", "duplicate_key": "",
            "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["blank_target_paths"] == 1
        assert result["valid"] is False
        assert any("blank proposed_target_path" in e for e in result["errors"])

    def test_whitespace_only_target_path_invalidates(self, tmp_path: Path):
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
        assert result["blank_target_paths"] == 1
        assert result["valid"] is False

    def test_existing_tier500_blank_target_path_invalidates(self, tmp_path: Path):
        src = tmp_path / "src.jpg"
        src.write_bytes(b"\xff\xd8" + b"\x00" * 2000)
        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        rows = [{
            "row_id": "1", "source_path": str(src),
            "proposed_target_path": "",
            "extension": ".jpg", "size_bytes": "2002",
            "selection_reason": "existing_tier500", "duplicate_key": "",
            "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["blank_target_paths"] == 1
        assert result["valid"] is False

    def test_excluded_row_blank_target_ok(self, tmp_path: Path):
        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        rows = [{
            "row_id": "1", "source_path": str(tmp_path / "nonexistent.mp4"),
            "proposed_target_path": "",
            "extension": ".mp4", "size_bytes": "5000",
            "selection_reason": "", "duplicate_key": "",
            "exclusion_reason": "unsupported_format:.mp4",
            "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["blank_target_paths"] == 0
        assert result["valid"] is True


# ---------------------------------------------------------------------------
# 11. Selection reason validation (copy-safety)
# ---------------------------------------------------------------------------

class TestSelectionReasonValidation:
    def test_unknown_selection_reason_invalidates(self, tmp_path: Path):
        src = tmp_path / "img.jpg"
        src.write_bytes(b"\xff\xd8" + b"\x00" * 2000)
        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        rows = [{
            "row_id": "1", "source_path": str(src),
            "proposed_target_path": str(target_root / "img.jpg"),
            "extension": ".jpg", "size_bytes": "2002",
            "selection_reason": "random_pick", "duplicate_key": "",
            "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["valid"] is False
        assert result["invalid_selection_reasons"] == 1
        assert any("invalid selection_reason" in e for e in result["errors"])

    def test_blank_selection_reason_on_non_excluded_invalidates(self, tmp_path: Path):
        src = tmp_path / "img.jpg"
        src.write_bytes(b"\xff\xd8" + b"\x00" * 2000)
        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        rows = [{
            "row_id": "1", "source_path": str(src),
            "proposed_target_path": str(target_root / "img.jpg"),
            "extension": ".jpg", "size_bytes": "2002",
            "selection_reason": "", "duplicate_key": "",
            "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["valid"] is False
        assert result["invalid_selection_reasons"] == 1


# ---------------------------------------------------------------------------
# 12. Exclusion reason validation (copy-safety)
# ---------------------------------------------------------------------------

class TestExclusionReasonValidation:
    def test_whitespace_only_exclusion_not_excluded(self, tmp_path: Path):
        """Whitespace-only exclusion_reason is stripped to empty, so the row
        is treated as non-excluded. With a valid selection_reason and real
        source file, it should be processed as a copy row."""
        src = tmp_path / "img.jpg"
        src.write_bytes(b"\xff\xd8" + b"\x00" * 2000)
        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        rows = [{
            "row_id": "1", "source_path": str(src),
            "proposed_target_path": str(target_root / "img.jpg"),
            "extension": ".jpg", "size_bytes": "2002",
            "selection_reason": "new_candidate", "duplicate_key": "",
            "exclusion_reason": "   ", "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["excluded_rows"] == 0
        assert result["new_candidate_rows"] == 1
        assert result["valid"] is True

    def test_unknown_exclusion_reason_invalidates(self, tmp_path: Path):
        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        rows = [{
            "row_id": "1", "source_path": str(tmp_path / "img.jpg"),
            "proposed_target_path": str(target_root / "img.jpg"),
            "extension": ".jpg", "size_bytes": "5000",
            "selection_reason": "", "duplicate_key": "",
            "exclusion_reason": "bad_reason", "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["valid"] is False
        assert result["invalid_exclusion_reasons"] == 1
        assert any("invalid exclusion_reason" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# 13. Extension cross-validation (copy-safety)
# ---------------------------------------------------------------------------

class TestExtensionCrossValidation:
    def test_csv_extension_spoofing_invalidates(self, tmp_path: Path):
        """CSV says .jpg but source file is .exe — must be caught."""
        src = tmp_path / "malware.exe"
        src.write_bytes(b"MZ" + b"\x00" * 5000)
        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        rows = [{
            "row_id": "1", "source_path": str(src),
            "proposed_target_path": str(target_root / "malware.jpg"),
            "extension": ".jpg", "size_bytes": "5002",
            "selection_reason": "new_candidate", "duplicate_key": "",
            "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["valid"] is False
        assert result["extension_mismatches"] > 0
        assert any("extension mismatch" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# 14. Collision by full resolved target path (copy-safety)
# ---------------------------------------------------------------------------

class TestCollisionByFullPath:
    def test_different_subdirs_same_basename_no_collision(self, tmp_path: Path):
        """Two targets in different subdirs with the same basename are NOT collisions."""
        src1 = tmp_path / "a.jpg"
        src2 = tmp_path / "b.jpg"
        src1.write_bytes(b"\xff\xd8" + b"\x00" * 2000)
        src2.write_bytes(b"\xff\xd8" + b"\x00" * 3000)

        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        rows = [
            {
                "row_id": "1", "source_path": str(src1),
                "proposed_target_path": str(target_root / "subA" / "img.jpg"),
                "extension": ".jpg", "size_bytes": "2002",
                "selection_reason": "new_candidate", "duplicate_key": "",
                "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
            },
            {
                "row_id": "2", "source_path": str(src2),
                "proposed_target_path": str(target_root / "subB" / "img.jpg"),
                "extension": ".jpg", "size_bytes": "3002",
                "selection_reason": "new_candidate", "duplicate_key": "",
                "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
            },
        ]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["target_filename_collisions"] == 0
        assert result["valid"] is True

    def test_same_full_path_collision_detected(self, tmp_path: Path):
        """Two rows with the same full resolved target path → collision."""
        src1 = tmp_path / "a.jpg"
        src2 = tmp_path / "b.jpg"
        src1.write_bytes(b"\xff\xd8" + b"\x00" * 2000)
        src2.write_bytes(b"\xff\xd8" + b"\x00" * 3000)

        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        same_target = str(target_root / "subA" / "img.jpg")
        rows = [
            {
                "row_id": "1", "source_path": str(src1),
                "proposed_target_path": same_target,
                "extension": ".jpg", "size_bytes": "2002",
                "selection_reason": "new_candidate", "duplicate_key": "",
                "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
            },
            {
                "row_id": "2", "source_path": str(src2),
                "proposed_target_path": same_target,
                "extension": ".jpg", "size_bytes": "3002",
                "selection_reason": "new_candidate", "duplicate_key": "",
                "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
            },
        ]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["target_filename_collisions"] == 1
        assert result["valid"] is False
        assert any("collision" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# 15. Path.resolve() failure handling (copy-safety)
# ---------------------------------------------------------------------------

class TestResolveFailureHandling:
    def test_target_root_resolve_failure_invalidates(self, tmp_path: Path):
        """If target_root.resolve() throws RuntimeError, result is invalid (not crash)."""
        from unittest.mock import patch

        src = tmp_path / "src.jpg"
        src.write_bytes(b"\xff\xd8" + b"\x00" * 2000)
        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        rows = [{
            "row_id": "1", "source_path": str(src),
            "proposed_target_path": str(target_root / "img.jpg"),
            "extension": ".jpg", "size_bytes": "2002",
            "selection_reason": "new_candidate", "duplicate_key": "",
            "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        original_resolve = Path.resolve

        def _broken_resolve(self, *args, **kwargs):
            if self == target_root:
                raise RuntimeError("Simulated symlink loop")
            return original_resolve(self, *args, **kwargs)

        with patch.object(Path, "resolve", _broken_resolve):
            result = validate_manifest(manifest_path, target_root)
        assert result["valid"] is False
        assert any("resolve" in e.lower() for e in result["errors"])
