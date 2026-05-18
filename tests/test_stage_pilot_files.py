"""Tests for scripts/stage_pilot_files.py — staging manifest validator and executor."""
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
execute_copy = _module.execute_copy
post_copy_audit = _module.post_copy_audit
_clean_field = _module._clean_field


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

class TestExecuteCLISafety:
    def test_execute_without_confirm_exits_2(self, valid_manifest):
        """--execute without --confirm-copy-tier1000 -> exit 2."""
        manifest_path, target_root = valid_manifest
        r = _run(["--manifest", str(manifest_path), "--target-root", str(target_root), "--execute"])
        assert r.returncode == 2
        assert "--confirm-copy-tier1000" in r.stderr

    def test_execute_without_source_root_exits_2(self, valid_manifest):
        """--execute --confirm-copy-tier1000 without --source-root -> exit 2."""
        manifest_path, target_root = valid_manifest
        r = _run(["--manifest", str(manifest_path), "--target-root", str(target_root),
                  "--execute", "--confirm-copy-tier1000"])
        assert r.returncode == 2
        assert "--source-root" in r.stderr

    def test_execute_and_dryrun_mutually_exclusive(self, valid_manifest):
        """--execute and --dry-run together -> exit 2."""
        manifest_path, target_root = valid_manifest
        r = _run(["--manifest", str(manifest_path), "--target-root", str(target_root),
                  "--execute", "--dry-run", "--confirm-copy-tier1000",
                  "--source-root", str(target_root), "--existing-root", str(target_root)])
        assert r.returncode == 2
        assert "mutually exclusive" in r.stderr.lower()

    def test_no_mode_flag_exits_2(self, valid_manifest):
        """Neither --dry-run nor --execute -> exit 2."""
        manifest_path, target_root = valid_manifest
        r = _run(["--manifest", str(manifest_path), "--target-root", str(target_root)])
        assert r.returncode == 2
        assert "--dry-run" in r.stderr or "--execute" in r.stderr


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
# 15. Target existing files on disk (Codex P1 round 2)
# ---------------------------------------------------------------------------

class TestTargetExistingFiles:
    def test_target_already_exists_invalidates(self, tmp_path: Path):
        """If proposed_target_path already exists on disk, valid=False."""
        src = tmp_path / "src.jpg"
        src.write_bytes(b"\xff\xd8" + b"\x00" * 2000)

        target_root = tmp_path / "target"
        target_root.mkdir()
        existing_target = target_root / "img.jpg"
        existing_target.write_bytes(b"\xff\xd8" + b"\x00" * 1000)

        manifest_path = tmp_path / "manifest.csv"
        rows = [{
            "row_id": "1", "source_path": str(src),
            "proposed_target_path": str(existing_target),
            "extension": ".jpg", "size_bytes": "2002",
            "selection_reason": "new_candidate", "duplicate_key": "",
            "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["valid"] is False
        assert result["target_existing_files"] == 1
        assert any("already exist" in e for e in result["errors"])

    def test_target_not_existing_passes(self, valid_manifest):
        """If proposed targets do not exist on disk, no target_existing_files error."""
        manifest_path, target_root = valid_manifest
        result = validate_manifest(manifest_path, target_root)
        assert result["target_existing_files"] == 0
        assert result["valid"] is True


# ---------------------------------------------------------------------------
# 16. Suffix consistency (Codex P2 round 2)
# ---------------------------------------------------------------------------

class TestSuffixConsistency:
    def test_target_without_suffix_invalidates(self, tmp_path: Path):
        """A copy row where proposed_target_path has no file extension → invalid."""
        src = tmp_path / "img.jpg"
        src.write_bytes(b"\xff\xd8" + b"\x00" * 2000)

        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        rows = [{
            "row_id": "1", "source_path": str(src),
            "proposed_target_path": str(target_root / "img_no_ext"),
            "extension": ".jpg", "size_bytes": "2002",
            "selection_reason": "new_candidate", "duplicate_key": "",
            "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["valid"] is False
        assert result["suffix_missing"] >= 1

    def test_source_jpg_target_png_invalidates(self, tmp_path: Path):
        """Source .jpg + target .png → extension mismatch → invalid."""
        src = tmp_path / "photo.jpg"
        src.write_bytes(b"\xff\xd8" + b"\x00" * 2000)

        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        rows = [{
            "row_id": "1", "source_path": str(src),
            "proposed_target_path": str(target_root / "photo.png"),
            "extension": ".jpg", "size_bytes": "2002",
            "selection_reason": "new_candidate", "duplicate_key": "",
            "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["valid"] is False
        assert result["extension_mismatches"] >= 1
        assert any("extension mismatch" in e for e in result["errors"])

    def test_matching_suffixes_pass(self, valid_manifest):
        """Valid manifest with matching source/CSV/target suffixes passes."""
        manifest_path, target_root = valid_manifest
        result = validate_manifest(manifest_path, target_root)
        assert result["extension_mismatches"] == 0
        assert result["suffix_missing"] == 0
        assert result["valid"] is True


# ---------------------------------------------------------------------------
# 17. Path.resolve() failure handling (copy-safety) [renumbered from 15]
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


# ---------------------------------------------------------------------------
# 18. _clean_field helper (Phase 3.3b)
# ---------------------------------------------------------------------------

class TestCleanField:
    def test_normal_string(self):
        """Normal string value is returned stripped."""
        row = {"key": "  hello  "}
        assert _clean_field(row, "key") == "hello"

    def test_none_value(self):
        """None value returns default."""
        row = {"key": None}
        assert _clean_field(row, "key") == ""

    def test_missing_key(self):
        """Missing key returns default."""
        row = {}
        assert _clean_field(row, "key") == ""

    def test_custom_default(self):
        """Custom default is returned for missing key."""
        row = {}
        assert _clean_field(row, "key", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# 19. Blank extension validation (Phase 3.3b)
# ---------------------------------------------------------------------------

class TestBlankExtensionValidation:
    def test_blank_extension_on_copy_row_invalidates(self, tmp_path: Path):
        """A non-excluded copy row with blank extension → valid=False."""
        src = tmp_path / "img.jpg"
        src.write_bytes(b"\xff\xd8" + b"\x00" * 2000)

        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        rows = [{
            "row_id": "1", "source_path": str(src),
            "proposed_target_path": str(target_root / "img.jpg"),
            "extension": "", "size_bytes": "2002",
            "selection_reason": "new_candidate", "duplicate_key": "",
            "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["blank_extensions"] == 1
        assert result["valid"] is False

    def test_blank_extension_on_excluded_row_ok(self, tmp_path: Path):
        """An excluded row with blank extension is fine (not a copy row)."""
        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        rows = [{
            "row_id": "1", "source_path": str(tmp_path / "whatever.xyz"),
            "proposed_target_path": "",
            "extension": "", "size_bytes": "100",
            "selection_reason": "", "duplicate_key": "",
            "exclusion_reason": "unsupported_format:.xyz",
            "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        result = validate_manifest(manifest_path, target_root)
        assert result["blank_extensions"] == 0
        assert result["excluded_rows"] == 1


# ---------------------------------------------------------------------------
# 20. Truncated rows (Phase 3.3b)
# ---------------------------------------------------------------------------

class TestTruncatedRows:
    def test_truncated_row_missing_fields_counted(self, tmp_path: Path):
        """A row missing required CSV fields is counted as truncated."""
        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"
        # Write a CSV with only 2 of the required fields
        with open(manifest_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["row_id", "source_path"])
            writer.writeheader()
            writer.writerow({"row_id": "1", "source_path": "/some/file.jpg"})

        result = validate_manifest(manifest_path, target_root)
        assert result["truncated_rows"] == 1
        assert result["valid"] is False
        assert any("truncated" in e.lower() for e in result["errors"])


# ---------------------------------------------------------------------------
# 21. Approved source roots validation (Phase 3.3b)
# ---------------------------------------------------------------------------

class TestApprovedSourceRoots:
    def test_source_outside_approved_root_invalidates(self, tmp_path: Path):
        """Source path outside approved roots → valid=False."""
        # Create source file outside the "approved" root
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        src = outside_dir / "img.jpg"
        src.write_bytes(b"\xff\xd8" + b"\x00" * 2000)

        approved_root = tmp_path / "approved"
        approved_root.mkdir()

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

        result = validate_manifest(manifest_path, target_root, approved_source_roots=[approved_root])
        assert result["source_root_violations"] == 1
        assert result["valid"] is False

    def test_source_inside_approved_root_passes(self, tmp_path: Path):
        """Source path inside approved root → no violation."""
        approved_root = tmp_path / "approved"
        approved_root.mkdir()
        src = approved_root / "img.jpg"
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

        result = validate_manifest(manifest_path, target_root, approved_source_roots=[approved_root])
        assert result["source_root_violations"] == 0
        assert result["valid"] is True

    def test_no_approved_roots_skips_check(self, valid_manifest):
        """When approved_source_roots=None, source root check is skipped."""
        manifest_path, target_root = valid_manifest
        result = validate_manifest(manifest_path, target_root, approved_source_roots=None)
        assert result["source_root_violations"] == 0
        assert result["valid"] is True


# ---------------------------------------------------------------------------
# 22. execute_copy (Phase 3.3b)
# ---------------------------------------------------------------------------

class TestExecuteCopy:
    def _make_manifest_and_files(self, tmp_path: Path, rows_data: list[dict] | None = None):
        """Helper to create a manifest + source files for copy tests."""
        source_root = tmp_path / "source"
        source_root.mkdir()
        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"

        if rows_data is None:
            # Default: two valid source files
            src1 = source_root / "img1.jpg"
            src2 = source_root / "img2.png"
            src1.write_bytes(b"\xff\xd8" + b"\x00" * 2000)
            src2.write_bytes(b"\x89PNG" + b"\x00" * 3000)
            rows_data = [
                {
                    "row_id": "1", "source_path": str(src1),
                    "proposed_target_path": str(target_root / "img1.jpg"),
                    "extension": ".jpg", "size_bytes": str(src1.stat().st_size),
                    "selection_reason": "new_candidate", "duplicate_key": "",
                    "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
                },
                {
                    "row_id": "2", "source_path": str(src2),
                    "proposed_target_path": str(target_root / "img2.png"),
                    "extension": ".png", "size_bytes": str(src2.stat().st_size),
                    "selection_reason": "new_candidate", "duplicate_key": "",
                    "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
                },
            ]

        _write_manifest(manifest_path, rows_data)
        return manifest_path, target_root, source_root

    def test_successful_copy(self, tmp_path: Path):
        """Normal copy: 2 files → both copied, correct byte count."""
        manifest_path, target_root, source_root = self._make_manifest_and_files(tmp_path)

        res = execute_copy(manifest_path, target_root, approved_source_roots=[source_root])
        assert res["copied"] == 2
        assert res["failed"] == 0
        assert res["total_bytes_copied"] > 0
        assert (target_root / "img1.jpg").is_file()
        assert (target_root / "img2.png").is_file()

    def test_copy_skips_excluded_rows(self, tmp_path: Path):
        """Excluded rows are skipped, not copied."""
        source_root = tmp_path / "source"
        source_root.mkdir()
        src = source_root / "img.jpg"
        src.write_bytes(b"\xff\xd8" + b"\x00" * 2000)
        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"

        rows = [
            {
                "row_id": "1", "source_path": str(src),
                "proposed_target_path": str(target_root / "img.jpg"),
                "extension": ".jpg", "size_bytes": str(src.stat().st_size),
                "selection_reason": "new_candidate", "duplicate_key": "",
                "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
            },
            {
                "row_id": "2", "source_path": str(source_root / "gone.gif"),
                "proposed_target_path": "",
                "extension": ".gif", "size_bytes": "0",
                "selection_reason": "", "duplicate_key": "",
                "exclusion_reason": "placeholder",
                "placeholder_flag": "True", "stat_error": "False",
            },
        ]
        _write_manifest(manifest_path, rows)

        res = execute_copy(manifest_path, target_root, approved_source_roots=[source_root])
        assert res["copied"] == 1
        assert res["skipped_excluded"] == 1
        assert res["failed"] == 0

    def test_copy_stops_on_missing_source(self, tmp_path: Path):
        """Missing source file → copy stops with error."""
        source_root = tmp_path / "source"
        source_root.mkdir()
        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"

        rows = [{
            "row_id": "1", "source_path": str(source_root / "nonexistent.jpg"),
            "proposed_target_path": str(target_root / "img.jpg"),
            "extension": ".jpg", "size_bytes": "2000",
            "selection_reason": "new_candidate", "duplicate_key": "",
            "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        res = execute_copy(manifest_path, target_root, approved_source_roots=[source_root])
        assert res["failed"] >= 1
        assert res["copied"] == 0
        assert "not found" in res["failed_reason"].lower()

    def test_copy_refuses_overwrite(self, tmp_path: Path):
        """If target already exists, copy refuses (never overwrite)."""
        source_root = tmp_path / "source"
        source_root.mkdir()
        src = source_root / "img.jpg"
        src.write_bytes(b"\xff\xd8" + b"\x00" * 2000)

        target_root = tmp_path / "target"
        target_root.mkdir(parents=True)
        existing_target = target_root / "img.jpg"
        existing_target.write_bytes(b"existing content")

        manifest_path = tmp_path / "manifest.csv"
        rows = [{
            "row_id": "1", "source_path": str(src),
            "proposed_target_path": str(existing_target),
            "extension": ".jpg", "size_bytes": str(src.stat().st_size),
            "selection_reason": "new_candidate", "duplicate_key": "",
            "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        res = execute_copy(manifest_path, target_root, approved_source_roots=[source_root])
        assert res["failed"] >= 1
        assert res["copied"] == 0
        assert "overwrite" in res["failed_reason"].lower() or "exists" in res["failed_reason"].lower()

    def test_copy_rejects_source_outside_approved_root(self, tmp_path: Path):
        """Source file outside approved roots → copy fails."""
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        src = outside_dir / "img.jpg"
        src.write_bytes(b"\xff\xd8" + b"\x00" * 2000)

        approved_root = tmp_path / "approved"
        approved_root.mkdir()
        target_root = tmp_path / "target"
        manifest_path = tmp_path / "manifest.csv"

        rows = [{
            "row_id": "1", "source_path": str(src),
            "proposed_target_path": str(target_root / "img.jpg"),
            "extension": ".jpg", "size_bytes": str(src.stat().st_size),
            "selection_reason": "new_candidate", "duplicate_key": "",
            "exclusion_reason": "", "placeholder_flag": "False", "stat_error": "False",
        }]
        _write_manifest(manifest_path, rows)

        res = execute_copy(manifest_path, target_root, approved_source_roots=[approved_root])
        assert res["failed"] >= 1
        assert res["copied"] == 0
        assert "approved" in res["failed_reason"].lower() or "outside" in res["failed_reason"].lower()


# ---------------------------------------------------------------------------
# 23. post_copy_audit (Phase 3.3b)
# ---------------------------------------------------------------------------

class TestPostCopyAudit:
    def test_audit_counts_files(self, tmp_path: Path):
        """Audit correctly counts files, bytes, and extensions."""
        target_root = tmp_path / "staged"
        target_root.mkdir()
        (target_root / "a.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 1000)
        (target_root / "b.png").write_bytes(b"\x89PNG" + b"\x00" * 2000)
        (target_root / "c.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 500)

        audit = post_copy_audit(target_root)
        assert audit["exists"] is True
        assert audit["total_files"] == 3
        assert audit["total_bytes"] > 0
        assert audit["extension_counts"][".jpg"] == 2
        assert audit["extension_counts"][".png"] == 1
        assert len(audit["unexpected_extensions"]) == 0

    def test_audit_nonexistent_dir(self, tmp_path: Path):
        """Audit of non-existent directory returns exists=False."""
        audit = post_copy_audit(tmp_path / "does_not_exist")
        assert audit["exists"] is False
        assert audit["total_files"] == 0

    def test_audit_detects_unexpected_extensions(self, tmp_path: Path):
        """Files with non-supported extensions are flagged."""
        target_root = tmp_path / "staged"
        target_root.mkdir()
        (target_root / "a.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 1000)
        (target_root / "readme.txt").write_bytes(b"hello")

        audit = post_copy_audit(target_root)
        assert audit["total_files"] == 2
        assert len(audit["unexpected_extensions"]) == 1
        assert any("readme.txt" in p for p in audit["unexpected_extensions"])
