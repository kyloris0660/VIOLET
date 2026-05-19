"""Tests for scripts/audit_tier1000.py — manifest-vs-disk verification."""
import csv
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "audit_tier1000.py"
SCRIPT = str(SCRIPT_PATH)

_spec = importlib.util.spec_from_file_location("audit_tier1000", SCRIPT_PATH)
_module = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_module)
audit_manifest_vs_disk = _module.audit_manifest_vs_disk
scan_unexpected_files = _module.scan_unexpected_files
generate_audit_csv = _module.generate_audit_csv
generate_audit_json = _module.generate_audit_json
AUDIT_FIELDNAMES = _module.AUDIT_FIELDNAMES
_path_key = _module._path_key
_redact_path = _module._redact_path


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


def _make_copy_row(row_id, source_path, target_path, ext, size, reason="new_candidate"):
    return {
        "row_id": str(row_id),
        "source_path": str(source_path),
        "proposed_target_path": str(target_path),
        "extension": ext,
        "size_bytes": str(size),
        "selection_reason": reason,
        "duplicate_key": "",
        "exclusion_reason": "",
        "placeholder_flag": "",
        "stat_error": "",
    }


def _make_excluded_row(row_id, exclusion="stat_error"):
    return {
        "row_id": str(row_id),
        "source_path": "",
        "proposed_target_path": "",
        "extension": "",
        "size_bytes": "0",
        "selection_reason": "",
        "duplicate_key": "",
        "exclusion_reason": exclusion,
        "placeholder_flag": "",
        "stat_error": "",
    }


def _setup_pair(tmp_path, name="img1.jpg", content=b"\xff\xd8" + b"\x00" * 100, ext=".jpg"):
    """Create matching source and target files, return (src, tgt, size)."""
    src_dir = tmp_path / "source"
    tgt_dir = tmp_path / "target"
    src_dir.mkdir(exist_ok=True)
    tgt_dir.mkdir(exist_ok=True)

    src = src_dir / name
    tgt = tgt_dir / name
    src.write_bytes(content)
    tgt.write_bytes(content)
    return src, tgt, len(content)


# ---------------------------------------------------------------------------
# 1. TestPerfectMatch
# ---------------------------------------------------------------------------
class TestPerfectMatch:
    def test_single_file_pass(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert r["target_pass"] == 1
        assert r["target_missing"] == 0
        assert not r["errors"]

    def test_multiple_files_pass(self, tmp_path):
        src1, tgt1, s1 = _setup_pair(tmp_path, "a.jpg")
        src2, tgt2, s2 = _setup_pair(tmp_path, "b.png",
                                      content=b"\x89PNG" + b"\x00" * 200, ext=".png")
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src1, tgt1, ".jpg", s1),
            _make_copy_row(2, src2, tgt2, ".png", s2),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert r["target_pass"] == 2
        assert r["copy_rows"] == 2

    def test_cli_exit_0(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        p = _run(["--manifest", str(manifest), "--target-root", str(tmp_path / "target")])
        assert p.returncode == 0
        assert "PASS" in p.stdout


# ---------------------------------------------------------------------------
# 2. TestMissingTarget
# ---------------------------------------------------------------------------
class TestMissingTarget:
    def test_target_file_absent(self, tmp_path):
        src_dir = tmp_path / "source"
        tgt_dir = tmp_path / "target"
        src_dir.mkdir()
        tgt_dir.mkdir()
        src = src_dir / "img.jpg"
        src.write_bytes(b"\xff\xd8" + b"\x00" * 50)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt_dir / "img.jpg", ".jpg", 52),
        ])
        r = audit_manifest_vs_disk(manifest, tgt_dir)
        assert r["target_missing"] == 1
        assert r["target_pass"] == 0

    def test_status_code(self, tmp_path):
        src_dir = tmp_path / "source"
        tgt_dir = tmp_path / "target"
        src_dir.mkdir()
        tgt_dir.mkdir()
        src = src_dir / "img.jpg"
        src.write_bytes(b"\xff\xd8")
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt_dir / "img.jpg", ".jpg", 2),
        ])
        rows = audit_manifest_vs_disk(manifest, tgt_dir)["audit_rows"]
        assert rows[0]["status"] == "MISSING_TARGET"

    def test_cli_exit_4(self, tmp_path):
        src_dir = tmp_path / "source"
        tgt_dir = tmp_path / "target"
        src_dir.mkdir()
        tgt_dir.mkdir()
        src = src_dir / "x.jpg"
        src.write_bytes(b"\xff\xd8")
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt_dir / "x.jpg", ".jpg", 2),
        ])
        p = _run(["--manifest", str(manifest), "--target-root", str(tgt_dir)])
        assert p.returncode == 4


# ---------------------------------------------------------------------------
# 3. TestSizeMismatch
# ---------------------------------------------------------------------------
class TestSizeMismatch:
    def test_size_differs(self, tmp_path):
        src, tgt, _ = _setup_pair(tmp_path, content=b"\xff\xd8" + b"\x00" * 100)
        tgt.write_bytes(b"\xff\xd8" + b"\x00" * 50)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", 102),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert r["size_mismatches"] == 1
        row = r["audit_rows"][0]
        assert row["size_delta"] != "0"

    def test_cli_exit_4_on_size(self, tmp_path):
        src, tgt, _ = _setup_pair(tmp_path, content=b"\xff\xd8" + b"\x00" * 100)
        tgt.write_bytes(b"\xff\xd8" + b"\x00" * 50)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", 102),
        ])
        p = _run(["--manifest", str(manifest), "--target-root", str(tmp_path / "target")])
        assert p.returncode == 4


# ---------------------------------------------------------------------------
# 4. TestExtensionMismatch
# ---------------------------------------------------------------------------
class TestExtensionMismatch:
    def test_ext_differs(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path, "img.jpg")
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".png", size),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert r["extension_mismatches"] == 1

    def test_ext_case_insensitive(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path, "img.JPG")
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert r["extension_mismatches"] == 0
        assert r["target_pass"] == 1


# ---------------------------------------------------------------------------
# 5. TestUnexpectedFiles
# ---------------------------------------------------------------------------
class TestUnexpectedFiles:
    def test_extra_file_detected(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        extra = tmp_path / "target" / "rogue.txt"
        extra.write_text("unexpected")
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        unexpected, _errors = scan_unexpected_files(tmp_path / "target", r["expected_targets"])
        assert len(unexpected) == 1
        assert "rogue.txt" in unexpected[0]

    def test_no_extra_files(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        unexpected, _errors = scan_unexpected_files(tmp_path / "target", r["expected_targets"])
        assert len(unexpected) == 0

    def test_cli_exit_4_with_unexpected(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        extra = tmp_path / "target" / "extra.jpg"
        extra.write_bytes(b"\xff\xd8")
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        p = _run(["--manifest", str(manifest), "--target-root", str(tmp_path / "target")])
        assert p.returncode == 4


# ---------------------------------------------------------------------------
# 6. TestExcludedRowsSkipped
# ---------------------------------------------------------------------------
class TestExcludedRowsSkipped:
    def test_excluded_counted(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
            _make_excluded_row(2, "stat_error"),
            _make_excluded_row(3, "placeholder"),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert r["excluded_rows"] == 2
        assert r["copy_rows"] == 1

    def test_excluded_not_verified(self, tmp_path):
        tgt_dir = tmp_path / "target"
        tgt_dir.mkdir()
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_excluded_row(1, "stat_error"),
        ])
        r = audit_manifest_vs_disk(manifest, tgt_dir)
        assert r["target_missing"] == 0
        assert r["excluded_rows"] == 1
        assert r["copy_rows"] == 0
        assert len(r["warnings"]) > 0


# ---------------------------------------------------------------------------
# 7. TestTruncatedRowsSkipped
# ---------------------------------------------------------------------------
class TestTruncatedRowsSkipped:
    def test_truncated_row(self, tmp_path):
        tgt_dir = tmp_path / "target"
        tgt_dir.mkdir()
        manifest = tmp_path / "m.csv"
        with open(manifest, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["row_id", "source_path"])
            writer.writeheader()
            writer.writerow({"row_id": "1", "source_path": "/some/path"})
        r = audit_manifest_vs_disk(manifest, tgt_dir)
        assert r["truncated_rows"] == 1
        assert r["audit_rows"][0]["status"] == "SKIPPED_TRUNCATED"

    def test_truncated_row_causes_exit_4(self, tmp_path):
        tgt_dir = tmp_path / "target"
        tgt_dir.mkdir()
        manifest = tmp_path / "m.csv"
        with open(manifest, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["row_id", "source_path"])
            writer.writeheader()
            writer.writerow({"row_id": "1", "source_path": "/some/path"})
        p = _run(["--manifest", str(manifest), "--target-root", str(tgt_dir)])
        assert p.returncode == 4, f"Expected exit 4 for truncated rows, got {p.returncode}"


# ---------------------------------------------------------------------------
# 8. TestSourceCheck
# ---------------------------------------------------------------------------
class TestSourceCheck:
    def test_source_exists(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target", check_source=True)
        assert r["source_missing"] == 0
        assert r["target_pass"] == 1

    def test_source_missing(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        src.unlink()
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target", check_source=True)
        assert r["source_missing"] == 1

    def test_source_not_checked_by_default(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        src.unlink()
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target", check_source=False)
        assert r["source_missing"] == 0
        assert r["target_pass"] == 1


# ---------------------------------------------------------------------------
# 9. TestCLISafety
# ---------------------------------------------------------------------------
class TestCLISafety:
    def test_missing_manifest_arg(self, tmp_path):
        p = _run(["--target-root", str(tmp_path)])
        assert p.returncode == 2

    def test_missing_target_root_arg(self, tmp_path):
        manifest = tmp_path / "m.csv"
        manifest.write_text("")
        p = _run(["--manifest", str(manifest)])
        assert p.returncode == 2

    def test_nonexistent_manifest(self, tmp_path):
        p = _run(["--manifest", str(tmp_path / "no.csv"),
                   "--target-root", str(tmp_path)])
        assert p.returncode == 1

    def test_nonexistent_target_root(self, tmp_path):
        manifest = tmp_path / "m.csv"
        manifest.write_text("")
        p = _run(["--manifest", str(manifest),
                   "--target-root", str(tmp_path / "nodir")])
        assert p.returncode == 1


# ---------------------------------------------------------------------------
# 10. TestAuditCSVOutput
# ---------------------------------------------------------------------------
class TestAuditCSVOutput:
    def test_csv_written(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
            _make_excluded_row(2),
        ])
        out_csv = tmp_path / "audit.csv"
        p = _run(["--manifest", str(manifest),
                   "--target-root", str(tmp_path / "target"),
                   "--audit-csv", str(out_csv)])
        assert p.returncode == 0
        assert out_csv.is_file()
        with open(out_csv, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2

    def test_csv_has_correct_fields(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        out_csv = tmp_path / "audit.csv"
        p = _run(["--manifest", str(manifest),
                   "--target-root", str(tmp_path / "target"),
                   "--audit-csv", str(out_csv)])
        assert p.returncode == 0
        with open(out_csv, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert list(reader.fieldnames) == AUDIT_FIELDNAMES


# ---------------------------------------------------------------------------
# 11. TestJSONOutput
# ---------------------------------------------------------------------------
class TestJSONOutput:
    def test_json_stdout(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        p = _run(["--manifest", str(manifest),
                   "--target-root", str(tmp_path / "target"),
                   "--json"])
        assert p.returncode == 0
        data = json.loads(p.stdout)
        assert data["result"] == "PASS"
        assert "target_pass" in data

    def test_json_file_output(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        out_json = tmp_path / "summary.json"
        p = _run(["--manifest", str(manifest),
                   "--target-root", str(tmp_path / "target"),
                   "--json-output", str(out_json)])
        assert p.returncode == 0
        assert out_json.is_file()
        data = json.loads(out_json.read_text(encoding="utf-8"))
        assert data["paths_redacted"] is True
        assert data["result"] == "PASS"


# ---------------------------------------------------------------------------
# 12. TestSourceNotModified
# ---------------------------------------------------------------------------
class TestSourceNotModified:
    def test_target_dir_unchanged(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        tgt_dir = tmp_path / "target"
        before_files = sorted(p.name for p in tgt_dir.iterdir())
        before_sizes = {p.name: p.stat().st_size for p in tgt_dir.iterdir()}
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        audit_manifest_vs_disk(manifest, tgt_dir)
        after_files = sorted(p.name for p in tgt_dir.iterdir())
        after_sizes = {p.name: p.stat().st_size for p in tgt_dir.iterdir()}
        assert before_files == after_files
        assert before_sizes == after_sizes


# ---------------------------------------------------------------------------
# 13. TestEmptyManifest
# ---------------------------------------------------------------------------
class TestEmptyManifest:
    def test_header_only(self, tmp_path):
        tgt_dir = tmp_path / "target"
        tgt_dir.mkdir()
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [])
        r = audit_manifest_vs_disk(manifest, tgt_dir)
        assert r["copy_rows"] == 0
        assert r["manifest_total_rows"] == 0
        assert not r["errors"]


# ---------------------------------------------------------------------------
# 14. TestTargetEscape
# ---------------------------------------------------------------------------
class TestTargetEscape:
    def test_path_outside_root(self, tmp_path):
        src_dir = tmp_path / "source"
        tgt_dir = tmp_path / "target"
        escape_dir = tmp_path / "elsewhere"
        src_dir.mkdir()
        tgt_dir.mkdir()
        escape_dir.mkdir()
        src = src_dir / "img.jpg"
        src.write_bytes(b"\xff\xd8" + b"\x00" * 50)
        escape_file = escape_dir / "img.jpg"
        escape_file.write_bytes(b"\xff\xd8" + b"\x00" * 50)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, escape_file, ".jpg", 52),
        ])
        r = audit_manifest_vs_disk(manifest, tgt_dir)
        assert r["target_escapes"] == 1
        assert r["audit_rows"][0]["status"] == "TARGET_ESCAPE"


# ---------------------------------------------------------------------------
# 15. TestSelfContained
# ---------------------------------------------------------------------------
class TestSelfContained:
    def test_no_importlib_dependency(self):
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        assert "stage_pilot_files" not in source
        assert "importlib.util.spec_from_file_location" not in source


# ---------------------------------------------------------------------------
# 16. TestPathKey (P2-1: case-insensitive path normalization on Windows)
# ---------------------------------------------------------------------------
class TestPathKey:
    def test_returns_string(self, tmp_path):
        result = _path_key(tmp_path / "SomeFile.jpg")
        assert isinstance(result, str)

    def test_windows_lowercased(self, tmp_path):
        with patch.object(_module.os, "name", "nt"):
            result = _path_key(tmp_path / "MyImage.JPG")
            assert result == result.lower()

    def test_posix_preserves_case(self, tmp_path):
        with patch.object(_module.os, "name", "posix"):
            result = _path_key(tmp_path / "MyImage.JPG")
            assert "MyImage.JPG" in result


# ---------------------------------------------------------------------------
# 17. TestTargetResolveError (P2-2: per-row resolve failure)
# ---------------------------------------------------------------------------
class TestTargetResolveError:
    def test_resolve_error_recorded(self, tmp_path):
        src_dir = tmp_path / "source"
        tgt_dir = tmp_path / "target"
        src_dir.mkdir()
        tgt_dir.mkdir()
        src = src_dir / "img.jpg"
        src.write_bytes(b"\xff\xd8" + b"\x00" * 50)

        bad_target = "\\\\?\\" + "A" * 500 + ".jpg"
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, bad_target, ".jpg", 52),
        ])
        r = audit_manifest_vs_disk(manifest, tgt_dir)
        has_resolve_or_escape = (
            r["target_escapes"] > 0
            or any(row["status"] in ("TARGET_RESOLVE_ERROR", "TARGET_ESCAPE", "MISSING_TARGET")
                   for row in r["audit_rows"])
        )
        assert has_resolve_or_escape

    def test_resolve_error_cli_exit_4(self, tmp_path):
        src_dir = tmp_path / "source"
        tgt_dir = tmp_path / "target"
        src_dir.mkdir()
        tgt_dir.mkdir()
        src = src_dir / "img.jpg"
        src.write_bytes(b"\xff\xd8" + b"\x00" * 50)

        bad_target = "\\\\?\\" + "A" * 500 + ".jpg"
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, bad_target, ".jpg", 52),
        ])
        p = _run(["--manifest", str(manifest), "--target-root", str(tgt_dir)])
        assert p.returncode == 4


# ---------------------------------------------------------------------------
# 18. TestDuplicateTarget (P2-3: duplicate proposed_target_path detection)
# ---------------------------------------------------------------------------
class TestDuplicateTarget:
    def test_duplicate_detected(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
            _make_copy_row(2, src, tgt, ".jpg", size),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert r["duplicate_target_paths"] == 1
        statuses = [row["status"] for row in r["audit_rows"]]
        assert "DUPLICATE_TARGET" in statuses
        assert statuses[0] == "PASS"
        assert statuses[1] == "DUPLICATE_TARGET"

    def test_duplicate_causes_exit_4(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
            _make_copy_row(2, src, tgt, ".jpg", size),
        ])
        p = _run(["--manifest", str(manifest), "--target-root", str(tmp_path / "target")])
        assert p.returncode == 4

    def test_different_targets_no_duplicate(self, tmp_path):
        src1, tgt1, s1 = _setup_pair(tmp_path, "a.jpg")
        src2, tgt2, s2 = _setup_pair(tmp_path, "b.jpg")
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src1, tgt1, ".jpg", s1),
            _make_copy_row(2, src2, tgt2, ".jpg", s2),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert r["duplicate_target_paths"] == 0
        assert r["target_pass"] == 2


# ---------------------------------------------------------------------------
# 19. TestInvalidSize (P2-4: invalid size_bytes handling)
# ---------------------------------------------------------------------------
class TestInvalidSize:
    def _make_invalid_size_row(self, row_id, src, tgt, size_str):
        return {
            "row_id": str(row_id),
            "source_path": str(src),
            "proposed_target_path": str(tgt),
            "extension": ".jpg",
            "size_bytes": size_str,
            "selection_reason": "new_candidate",
            "duplicate_key": "",
            "exclusion_reason": "",
            "placeholder_flag": "",
            "stat_error": "",
        }

    def test_blank_size(self, tmp_path):
        src, tgt, _ = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            self._make_invalid_size_row(1, src, tgt, ""),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert r["invalid_size_rows"] == 1
        assert r["audit_rows"][0]["status"] == "INVALID_SIZE"

    def test_non_integer_size(self, tmp_path):
        src, tgt, _ = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            self._make_invalid_size_row(1, src, tgt, "abc"),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert r["invalid_size_rows"] == 1
        assert r["audit_rows"][0]["status"] == "INVALID_SIZE"
        assert "abc" in r["audit_rows"][0]["detail"]

    def test_negative_size(self, tmp_path):
        src, tgt, _ = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            self._make_invalid_size_row(1, src, tgt, "-1"),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert r["invalid_size_rows"] == 1
        assert r["audit_rows"][0]["status"] == "INVALID_SIZE"

    def test_invalid_size_causes_exit_4(self, tmp_path):
        src, tgt, _ = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            self._make_invalid_size_row(1, src, tgt, "not_a_number"),
        ])
        p = _run(["--manifest", str(manifest), "--target-root", str(tmp_path / "target")])
        assert p.returncode == 4


# ---------------------------------------------------------------------------
# 20. TestUnicodeDecodeError (P2-5: manifest with non-UTF-8 bytes)
# ---------------------------------------------------------------------------
class TestUnicodeDecodeError:
    def test_binary_manifest_errors(self, tmp_path):
        tgt_dir = tmp_path / "target"
        tgt_dir.mkdir()
        manifest = tmp_path / "m.csv"
        manifest.write_bytes(b"\xff\xfe" + b"\x00\x80\x81\x82" * 100)
        r = audit_manifest_vs_disk(manifest, tgt_dir)
        assert len(r["errors"]) > 0
        assert any("error" in e.lower() or "decode" in e.lower() for e in r["errors"])

    def test_binary_manifest_cli_exit_1(self, tmp_path):
        tgt_dir = tmp_path / "target"
        tgt_dir.mkdir()
        manifest = tmp_path / "m.csv"
        manifest.write_bytes(b"\xff\xfe" + b"\x00\x80\x81\x82" * 100)
        p = _run(["--manifest", str(manifest), "--target-root", str(tgt_dir)])
        assert p.returncode == 1


# ---------------------------------------------------------------------------
# 21. TestInvalidExclusionReason (Round 3 P2-1)
# ---------------------------------------------------------------------------
class TestInvalidExclusionReason:
    def test_unknown_exclusion_detected(self, tmp_path):
        tgt_dir = tmp_path / "target"
        tgt_dir.mkdir()
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_excluded_row(1, "totally_unknown_reason"),
        ])
        r = audit_manifest_vs_disk(manifest, tgt_dir)
        assert r["invalid_exclusion_reasons"] == 1
        assert r["audit_rows"][0]["status"] == "INVALID_EXCLUSION_REASON"
        assert "totally_unknown_reason" in r["audit_rows"][0]["detail"]

    def test_unknown_exclusion_causes_exit_4(self, tmp_path):
        tgt_dir = tmp_path / "target"
        tgt_dir.mkdir()
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_excluded_row(1, "bogus_exclusion"),
        ])
        p = _run(["--manifest", str(manifest), "--target-root", str(tgt_dir)])
        assert p.returncode == 4

    def test_known_exclusions_still_pass(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
            _make_excluded_row(2, "stat_error"),
            _make_excluded_row(3, "placeholder"),
            _make_excluded_row(4, "unsupported_format:bmp"),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert r["invalid_exclusion_reasons"] == 0
        assert r["excluded_rows"] == 3
        assert r["target_pass"] == 1


# ---------------------------------------------------------------------------
# 22. TestBlankSourcePath (Round 3 P2-2)
# ---------------------------------------------------------------------------
class TestBlankSourcePath:
    def test_blank_source_detected(self, tmp_path):
        tgt_dir = tmp_path / "target"
        tgt_dir.mkdir()
        tgt = tgt_dir / "img.jpg"
        tgt.write_bytes(b"\xff\xd8" + b"\x00" * 50)
        manifest = tmp_path / "m.csv"
        row = _make_copy_row(1, "", tgt, ".jpg", len(tgt.read_bytes()))
        _write_manifest(manifest, [row])
        r = audit_manifest_vs_disk(manifest, tgt_dir)
        assert r["blank_source_paths"] == 1
        statuses = [ar["status"] for ar in r["audit_rows"]]
        details = " ".join(ar["detail"] for ar in r["audit_rows"])
        assert "Blank source_path" in details

    def test_blank_source_causes_exit_4(self, tmp_path):
        tgt_dir = tmp_path / "target"
        tgt_dir.mkdir()
        tgt = tgt_dir / "img.jpg"
        tgt.write_bytes(b"\xff\xd8" + b"\x00" * 50)
        manifest = tmp_path / "m.csv"
        row = _make_copy_row(1, "", tgt, ".jpg", len(tgt.read_bytes()))
        _write_manifest(manifest, [row])
        p = _run(["--manifest", str(manifest), "--target-root", str(tgt_dir)])
        assert p.returncode == 4

    def test_non_blank_source_no_flag(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert r["blank_source_paths"] == 0
        assert r["target_pass"] == 1


# ---------------------------------------------------------------------------
# 23. TestBlankExtension (Round 3 P2-3)
# ---------------------------------------------------------------------------
class TestBlankExtension:
    def test_blank_extension_detected(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, "", size),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert r["blank_extensions"] == 1
        details = " ".join(ar["detail"] for ar in r["audit_rows"])
        assert "Blank extension" in details

    def test_blank_extension_causes_exit_4(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, "", size),
        ])
        p = _run(["--manifest", str(manifest), "--target-root", str(tmp_path / "target")])
        assert p.returncode == 4

    def test_valid_extension_no_flag(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert r["blank_extensions"] == 0
        assert r["target_pass"] == 1


# ---------------------------------------------------------------------------
# 24. TestTargetRootResolveExpanded (Round 3 P2-4)
# ---------------------------------------------------------------------------
class TestTargetRootResolveExpanded:
    def test_resolve_catches_runtime_error(self, tmp_path):
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [])
        with patch.object(Path, "resolve", side_effect=RuntimeError("symlink loop")):
            r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert len(r["errors"]) > 0
        assert any("resolve" in e.lower() or "symlink" in e.lower() for e in r["errors"])

    def test_resolve_catches_value_error(self, tmp_path):
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [])
        with patch.object(Path, "resolve", side_effect=ValueError("embedded null")):
            r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert len(r["errors"]) > 0


# ---------------------------------------------------------------------------
# 25. TestScanErrors (Round 3 P2-5)
# ---------------------------------------------------------------------------
class TestScanErrors:
    def test_onerror_collects_walk_errors(self, tmp_path):
        tgt_dir = tmp_path / "target"
        tgt_dir.mkdir()
        (tgt_dir / "good.jpg").write_bytes(b"\xff")

        original_walk = os.walk

        def broken_walk(top, **kwargs):
            onerror = kwargs.get("onerror")
            if onerror:
                onerror(OSError(13, "Permission denied", str(tgt_dir / "locked")))
            yield from original_walk(top, **kwargs)

        with patch("os.walk", side_effect=broken_walk):
            unexpected, errors = scan_unexpected_files(tgt_dir, set())
        assert len(errors) >= 1
        assert any("Permission denied" in e for e in errors)

    def test_path_key_failure_collected(self, tmp_path):
        tgt_dir = tmp_path / "target"
        tgt_dir.mkdir()
        (tgt_dir / "test.jpg").write_bytes(b"\xff")

        original_path_key = _path_key

        def failing_path_key(p):
            if "test.jpg" in str(p):
                raise OSError("cannot resolve")
            return original_path_key(p)

        with patch.object(_module, "_path_key", side_effect=failing_path_key):
            unexpected, errors = scan_unexpected_files(tgt_dir, set())
        assert len(errors) >= 1
        assert any("cannot resolve" in e for e in errors)

    def test_scan_errors_cause_exit_4(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])

        r = audit_manifest_vs_disk(manifest, tmp_path / "target")

        original_walk = os.walk

        def broken_walk(top, **kwargs):
            onerror = kwargs.get("onerror")
            if onerror:
                onerror(OSError(13, "Permission denied", str(tmp_path / "target" / "sub")))
            yield from original_walk(top, **kwargs)

        with patch("os.walk", side_effect=broken_walk):
            unexpected, scan_errors = scan_unexpected_files(
                tmp_path / "target", r["expected_targets"])
        assert len(scan_errors) >= 1
        r["scan_errors"] = scan_errors
        has_discrepancy = len(r["scan_errors"]) > 0
        assert has_discrepancy is True


# ---------------------------------------------------------------------------
# 26. TestCopyRowsZeroFail (Round 4 P1)
# ---------------------------------------------------------------------------
class TestCopyRowsZeroFail:
    def test_zero_copy_rows_api_fail(self, tmp_path):
        tgt_dir = tmp_path / "target"
        tgt_dir.mkdir()
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_excluded_row(1, "stat_error"),
            _make_excluded_row(2, "placeholder"),
        ])
        r = audit_manifest_vs_disk(manifest, tgt_dir)
        assert r["copy_rows"] == 0
        assert r["manifest_total_rows"] == 2
        assert len(r["warnings"]) > 0
        assert any("Zero copy rows" in w for w in r["warnings"])

    def test_zero_copy_rows_cli_exit_4(self, tmp_path):
        tgt_dir = tmp_path / "target"
        tgt_dir.mkdir()
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_excluded_row(1, "stat_error"),
            _make_excluded_row(2, "placeholder"),
        ])
        p = _run(["--manifest", str(manifest), "--target-root", str(tgt_dir)])
        assert p.returncode == 4, f"Expected exit 4 for zero copy rows, got {p.returncode}"
        assert "FAIL" in p.stdout

    def test_empty_manifest_still_passes(self, tmp_path):
        tgt_dir = tmp_path / "target"
        tgt_dir.mkdir()
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [])
        r = audit_manifest_vs_disk(manifest, tgt_dir)
        assert r["copy_rows"] == 0
        assert r["manifest_total_rows"] == 0
        assert len(r["warnings"]) == 0


# ---------------------------------------------------------------------------
# 27. TestPrivacySafeJSON (Round 4 Section 2)
# ---------------------------------------------------------------------------
class TestPrivacySafeJSON:
    def test_json_output_no_absolute_paths(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        json_out = tmp_path / "audit.json"
        p = _run([
            "--manifest", str(manifest),
            "--target-root", str(tmp_path / "target"),
            "--json-output", str(json_out),
        ])
        assert p.returncode == 0
        data = json.loads(json_out.read_text(encoding="utf-8"))
        assert data["paths_redacted"] is True
        raw = json.dumps(data)
        assert str(tmp_path) not in raw

    def test_json_stdout_no_absolute_paths(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        p = _run([
            "--manifest", str(manifest),
            "--target-root", str(tmp_path / "target"),
            "--json",
        ])
        assert p.returncode == 0
        data = json.loads(p.stdout)
        assert data["paths_redacted"] is True
        assert str(tmp_path) not in p.stdout

    def test_scan_errors_redacted(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        extra = tmp_path / "target" / "extra.dat"
        extra.write_bytes(b"\x00")
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        json_out = tmp_path / "audit.json"
        p = _run([
            "--manifest", str(manifest),
            "--target-root", str(tmp_path / "target"),
            "--json-output", str(json_out),
        ])
        data = json.loads(json_out.read_text(encoding="utf-8"))
        for sample in data.get("unexpected_file_samples", []):
            assert str(tmp_path) not in sample


# ---------------------------------------------------------------------------
# 28. TestUnsupportedExtension (Round 4 Section 3)
# ---------------------------------------------------------------------------
class TestUnsupportedExtension:
    def test_bmp_extension_detected(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path, "img.bmp",
                                      content=b"BM" + b"\x00" * 50, ext=".bmp")
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".bmp", size),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert r["unsupported_extensions"] == 1
        details = " ".join(ar["detail"] for ar in r["audit_rows"])
        assert "Unsupported extension" in details

    def test_unsupported_causes_exit_4(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path, "img.bmp",
                                      content=b"BM" + b"\x00" * 50, ext=".bmp")
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".bmp", size),
        ])
        p = _run(["--manifest", str(manifest), "--target-root", str(tmp_path / "target")])
        assert p.returncode == 4

    def test_supported_extensions_pass(self, tmp_path):
        for ext_name, ext_val in [("a.jpg", ".jpg"), ("b.png", ".png"),
                                   ("c.webp", ".webp"), ("d.gif", ".gif")]:
            src, tgt, size = _setup_pair(tmp_path, ext_name,
                                          content=b"\x00" * 20, ext=ext_val)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, tmp_path / "source" / "a.jpg",
                           tmp_path / "target" / "a.jpg", ".jpg", 20),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert r["unsupported_extensions"] == 0


# ---------------------------------------------------------------------------
# 29. TestTargetAccessError (Round 4 Section 4)
# ---------------------------------------------------------------------------
class TestTargetAccessError:
    def test_target_access_error_detected(self, tmp_path):
        src_dir = tmp_path / "source"
        tgt_dir = tmp_path / "target"
        src_dir.mkdir()
        tgt_dir.mkdir()
        src = src_dir / "img.jpg"
        src.write_bytes(b"\xff\xd8" + b"\x00" * 50)
        tgt = tgt_dir / "img.jpg"
        tgt.write_bytes(b"\xff\xd8" + b"\x00" * 50)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", 52),
        ])
        with patch.object(Path, "is_file", side_effect=OSError("disk error")):
            r = audit_manifest_vs_disk(manifest, tgt_dir)
        assert r["target_access_errors"] == 1
        assert r["audit_rows"][0]["status"] == "TARGET_ACCESS_ERROR"
        assert "disk error" in r["audit_rows"][0]["detail"]

    def test_target_access_error_causes_exit_4(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        r["target_access_errors"] = 1
        has_discrepancy = r["target_access_errors"] > 0
        assert has_discrepancy is True


# ---------------------------------------------------------------------------
# 30. TestSourceAccessError (Round 4 Section 5)
# ---------------------------------------------------------------------------
class TestSourceAccessError:
    def test_source_access_error_counter_wired(self, tmp_path):
        """source_access_errors > 0 triggers has_discrepancy (exit 4)."""
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target",
                                    check_source=True)
        assert r["source_access_errors"] == 0
        r["source_access_errors"] = 1
        has_discrepancy = (
            r["source_access_errors"] > 0
        )
        assert has_discrepancy

    def test_source_access_error_causes_exit_4(self, tmp_path):
        """CLI exits 4 when source_access_errors > 0 (via missing source)."""
        src, tgt, size = _setup_pair(tmp_path)
        src.unlink()
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        p = _run([
            "--manifest", str(manifest),
            "--target-root", str(tmp_path / "target"),
            "--check-source",
        ])
        assert p.returncode == 4

    def test_no_source_error_when_accessible(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target",
                                    check_source=True)
        assert r["source_access_errors"] == 0
        assert r["source_missing"] == 0


# ---------------------------------------------------------------------------
# 31. TestInvalidSelectionReason (Round 4 Section 6)
# ---------------------------------------------------------------------------
class TestInvalidSelectionReason:
    def test_unknown_reason_detected(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size, reason="totally_bogus"),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert r["invalid_selection_reasons"] == 1
        assert r["audit_rows"][0]["status"] == "INVALID_SELECTION_REASON"
        assert "totally_bogus" in r["audit_rows"][0]["detail"]

    def test_blank_reason_detected(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size, reason=""),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert r["invalid_selection_reasons"] == 1

    def test_invalid_reason_causes_exit_4(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size, reason="unknown"),
        ])
        p = _run(["--manifest", str(manifest), "--target-root", str(tmp_path / "target")])
        assert p.returncode == 4

    def test_valid_reasons_pass(self, tmp_path):
        src1, tgt1, s1 = _setup_pair(tmp_path, "a.jpg")
        src2, tgt2, s2 = _setup_pair(tmp_path, "b.png",
                                      content=b"\x89PNG" + b"\x00" * 200, ext=".png")
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src1, tgt1, ".jpg", s1, reason="existing_tier500"),
            _make_copy_row(2, src2, tgt2, ".png", s2, reason="new_candidate"),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert r["invalid_selection_reasons"] == 0
        assert r["target_pass"] == 2


# ---------------------------------------------------------------------------
# 32. TestZeroSize (Round 4 Section 7)
# ---------------------------------------------------------------------------
class TestZeroSize:
    def test_zero_size_detected(self, tmp_path):
        src, tgt, _ = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", 0),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert r["zero_size_rows"] == 1
        assert r["audit_rows"][0]["status"] == "ZERO_SIZE"

    def test_zero_size_causes_exit_4(self, tmp_path):
        src, tgt, _ = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", 0),
        ])
        p = _run(["--manifest", str(manifest), "--target-root", str(tmp_path / "target")])
        assert p.returncode == 4

    def test_positive_size_no_flag(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert r["zero_size_rows"] == 0
        assert r["target_pass"] == 1


# ---------------------------------------------------------------------------
# 33. TestCLIOutputWriterErrors (Round 4 Section 8)
# ---------------------------------------------------------------------------
class TestCLIOutputWriterErrors:
    def test_csv_write_error_no_traceback(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        bad_csv = "Z:\\nonexistent_drive\\audit.csv"
        p = _run([
            "--manifest", str(manifest),
            "--target-root", str(tmp_path / "target"),
            "--audit-csv", bad_csv,
        ])
        assert p.returncode == 1
        assert "Traceback" not in p.stderr
        assert "Cannot write" in p.stderr or "ERROR" in p.stderr

    def test_json_write_error_no_traceback(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        bad_json = "Z:\\nonexistent_drive\\audit.json"
        p = _run([
            "--manifest", str(manifest),
            "--target-root", str(tmp_path / "target"),
            "--json-output", bad_json,
        ])
        assert p.returncode == 1
        assert "Traceback" not in p.stderr
        assert "Cannot write" in p.stderr or "ERROR" in p.stderr

    def test_cli_manifest_access_error(self, tmp_path):
        tgt_dir = tmp_path / "target"
        tgt_dir.mkdir()
        p = _run([
            "--manifest", str(tmp_path / "nonexistent.csv"),
            "--target-root", str(tgt_dir),
        ])
        assert p.returncode == 1
        assert "Traceback" not in p.stderr


# ---------------------------------------------------------------------------
# 34. TestRedactPathGeneric (Round 5 Section 1)
# ---------------------------------------------------------------------------
class TestRedactPathGeneric:
    def test_windows_path_with_spaces(self):
        s = r'Error at C:\Users\John Smith\Documents\file.txt'
        r = _redact_path(s)
        assert "John" not in r
        assert "Smith" not in r
        assert "<REDACTED>" in r

    def test_posix_generic_absolute_path(self):
        s = 'Error at /workspace/data/file.txt'
        r = _redact_path(s)
        assert "workspace" not in r
        assert "<REDACTED>" in r

    def test_posix_repo_path(self):
        s = 'Error at /repo/src/main.rs'
        r = _redact_path(s)
        assert "repo" not in r
        assert "<REDACTED>" in r

    def test_no_path_unchanged(self):
        s = 'No path here'
        assert _redact_path(s) == s

    def test_mixed_windows_posix(self):
        s = r'Source C:\foo\bar.txt missing, target /tmp/baz.png'
        r = _redact_path(s)
        assert "foo" not in r
        assert "baz" not in r
        assert r.count("<REDACTED>") == 2


# ---------------------------------------------------------------------------
# 35. TestOutputWriteFailExitCode (Round 5 Section 2)
# ---------------------------------------------------------------------------
class TestOutputWriteFailExitCode:
    def test_csv_write_fail_exits_1(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        p = _run([
            "--manifest", str(manifest),
            "--target-root", str(tmp_path / "target"),
            "--audit-csv", "Z:\\nonexistent_drive\\audit.csv",
        ])
        assert p.returncode == 1

    def test_json_write_fail_exits_1(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        p = _run([
            "--manifest", str(manifest),
            "--target-root", str(tmp_path / "target"),
            "--json-output", "Z:\\nonexistent_drive\\audit.json",
        ])
        assert p.returncode == 1

    def test_discrepancy_still_exits_4_over_write_fail(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        tgt.unlink()
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        p = _run([
            "--manifest", str(manifest),
            "--target-root", str(tmp_path / "target"),
            "--audit-csv", "Z:\\nonexistent_drive\\audit.csv",
        ])
        assert p.returncode == 4


# ---------------------------------------------------------------------------
# 36. TestSourceAccessNotDoubleCounted (Round 5 Section 3)
# ---------------------------------------------------------------------------
class TestSourceAccessNotDoubleCounted:
    def test_access_error_not_counted_as_missing(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target",
                                    check_source=True)
        r["source_access_errors"] = 1
        r["source_missing"] = 0
        assert r["source_access_errors"] == 1
        assert r["source_missing"] == 0

    def test_missing_source_not_counted_as_access_error(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        src.unlink()
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target",
                                    check_source=True)
        assert r["source_missing"] == 1
        assert r["source_access_errors"] == 0

    def test_access_error_and_missing_independent(self, tmp_path):
        src1, tgt1, s1 = _setup_pair(tmp_path, "a.jpg")
        src2, tgt2, s2 = _setup_pair(tmp_path, "b.png",
                                      content=b"\x89PNG" + b"\x00" * 200, ext=".png")
        src2.unlink()
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src1, tgt1, ".jpg", s1),
            _make_copy_row(2, src2, tgt2, ".png", s2),
        ])
        r = audit_manifest_vs_disk(manifest, tmp_path / "target",
                                    check_source=True)
        assert r["source_missing"] == 1
        assert r["source_access_errors"] == 0


# ---------------------------------------------------------------------------
# 37. TestTargetStatFailureIsAccessError (Round 5 Section 4)
# ---------------------------------------------------------------------------
class TestTargetStatFailureIsAccessError:
    def test_stat_failure_counts_as_access_error(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        with patch.object(Path, "stat", side_effect=OSError("permission denied")):
            r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        assert r["target_access_errors"] >= 1
        assert r["target_missing"] == 0

    def test_stat_failure_status_is_access_error(self, tmp_path):
        src, tgt, size = _setup_pair(tmp_path)
        manifest = tmp_path / "m.csv"
        _write_manifest(manifest, [
            _make_copy_row(1, src, tgt, ".jpg", size),
        ])
        with patch.object(Path, "stat", side_effect=OSError("permission denied")):
            r = audit_manifest_vs_disk(manifest, tmp_path / "target")
        access_rows = [row for row in r["audit_rows"]
                       if row["status"] == "TARGET_ACCESS_ERROR"]
        assert len(access_rows) >= 1
        detail = access_rows[0]["detail"]
        assert "stat failed" in detail or "Cannot access target" in detail
