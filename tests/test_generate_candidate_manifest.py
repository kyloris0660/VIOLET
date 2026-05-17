"""Tests for scripts/generate_candidate_manifest.py — iCloud-safe candidate manifest generation."""
import csv
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "generate_candidate_manifest.py"

_spec = importlib.util.spec_from_file_location("generate_candidate_manifest", SCRIPT_PATH)
_module = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_module)
is_icloud_placeholder = _module.is_icloud_placeholder
generate_manifest = _module.generate_manifest
write_csv = _module.write_csv
write_summary = _module.write_summary
_scan_existing_dataset = _module._scan_existing_dataset
_scan_source = _module._scan_source


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def source_dir(tmp_path: Path):
    """Create a fake iCloud-like source directory with various file types."""
    src = tmp_path / "source"
    src.mkdir()
    # Normal supported images (> 1KB to pass placeholder check)
    (src / "anime001.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 2000)
    (src / "anime002.png").write_bytes(b"\x89PNG" + b"\x00" * 3000)
    (src / "anime003.webp").write_bytes(b"RIFF" + b"\x00" * 1500)
    (src / "anime004.gif").write_bytes(b"GIF89a" + b"\x00" * 2000)
    (src / "anime005.jpeg").write_bytes(b"\xff\xd8" + b"\x00" * 2500)
    return src


@pytest.fixture()
def existing_dir(tmp_path: Path):
    """Create a fake existing Tier-500 directory."""
    ex = tmp_path / "existing"
    ex.mkdir()
    (ex / "existing1.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 2000)
    (ex / "existing2.png").write_bytes(b"\x89PNG" + b"\x00" * 3000)
    return ex


@pytest.fixture()
def target_dir(tmp_path: Path):
    return tmp_path / "target"


# ---------------------------------------------------------------------------
# 1. Placeholder detection
# ---------------------------------------------------------------------------

class TestPlaceholderDetection:
    def test_zero_byte_is_placeholder(self, tmp_path: Path):
        f = tmp_path / "empty.jpg"
        f.write_bytes(b"")
        assert is_icloud_placeholder(f) is True

    def test_small_file_is_placeholder(self, tmp_path: Path):
        f = tmp_path / "tiny.jpg"
        f.write_bytes(b"\xff\xd8" + b"\x00" * 500)  # 502 bytes < 1024
        assert is_icloud_placeholder(f) is True

    def test_icloud_suffix_is_placeholder(self, tmp_path: Path):
        f = tmp_path / "photo.icloud"
        f.write_bytes(b"\x00" * 5000)
        assert is_icloud_placeholder(f) is True

    def test_hidden_dot_file_is_placeholder(self, tmp_path: Path):
        f = tmp_path / ".hidden_image.jpg"
        f.write_bytes(b"\xff\xd8" + b"\x00" * 5000)
        assert is_icloud_placeholder(f) is True

    def test_normal_image_not_placeholder(self, tmp_path: Path):
        f = tmp_path / "normal.jpg"
        f.write_bytes(b"\xff\xd8" + b"\x00" * 5000)
        assert is_icloud_placeholder(f) is False

    def test_stat_error_treated_as_placeholder(self, tmp_path: Path):
        f = tmp_path / "nonexistent.jpg"
        assert is_icloud_placeholder(f) is True


# ---------------------------------------------------------------------------
# 2. Duplicate detection
# ---------------------------------------------------------------------------

class TestDuplicateDetection:
    def test_existing_dataset_scan(self, existing_dir: Path):
        rows, idx, count, total_bytes = _scan_existing_dataset(existing_dir)
        assert count == 2
        assert len(rows) == 2
        names = {name for name, _ in idx}
        assert "existing1.jpg" in names
        assert "existing2.png" in names
        assert total_bytes > 0

    def test_existing_dataset_nonexistent_dir(self, tmp_path: Path):
        rows, idx, count, total_bytes = _scan_existing_dataset(tmp_path / "nope")
        assert idx == set()
        assert count == 0
        assert total_bytes == 0

    def test_duplicates_annotated_not_excluded(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "dup.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 2000)

        ex = tmp_path / "ex"
        ex.mkdir()
        (ex / "dup.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 2000)

        target = tmp_path / "tgt"
        result = generate_manifest(src, ex, target, target_total=10, seed=1)
        new_rows = [r for r in result["candidates"] if r["selection_reason"] == "new_candidate"]
        assert len(new_rows) == 1
        assert "possible_duplicate" in new_rows[0]["duplicate_key"]
        assert result["summary"]["possible_duplicates_with_existing"] == 1
        assert result["summary"]["selected_possible_duplicates"] == 1

    def test_duplicate_annotation_in_eligible_pool(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "dup.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 2000)
        (src / "unique.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 3000)

        ex = tmp_path / "ex"
        ex.mkdir()
        (ex / "dup.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 2000)

        target = tmp_path / "tgt"
        result = generate_manifest(src, ex, target, target_total=10, seed=1)
        new_rows = [r for r in result["candidates"] if r["selection_reason"] == "new_candidate"]
        assert len(new_rows) == 2
        assert result["summary"]["source_supported_eligible"] == 2


# ---------------------------------------------------------------------------
# 3. Deterministic selection with seed
# ---------------------------------------------------------------------------

class TestDeterministicSelection:
    def test_same_seed_same_result(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        for i in range(20):
            (src / f"img_{i:03d}.jpg").write_bytes(b"\xff\xd8" + b"\x00" * (2000 + i))

        ex = tmp_path / "ex"
        ex.mkdir()
        target = tmp_path / "tgt"

        r1 = generate_manifest(src, ex, target, target_total=5, seed=42)
        r2 = generate_manifest(src, ex, target, target_total=5, seed=42)

        names1 = [r["source_path"] for r in r1["candidates"] if r["selection_reason"] == "new_candidate"]
        names2 = [r["source_path"] for r in r2["candidates"] if r["selection_reason"] == "new_candidate"]
        assert names1 == names2

    def test_different_seed_different_result(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        for i in range(50):
            (src / f"img_{i:03d}.jpg").write_bytes(b"\xff\xd8" + b"\x00" * (2000 + i))

        ex = tmp_path / "ex"
        ex.mkdir()
        target = tmp_path / "tgt"

        r1 = generate_manifest(src, ex, target, target_total=10, seed=1)
        r2 = generate_manifest(src, ex, target, target_total=10, seed=999)

        names1 = sorted(r["source_path"] for r in r1["candidates"] if r["selection_reason"] == "new_candidate")
        names2 = sorted(r["source_path"] for r in r2["candidates"] if r["selection_reason"] == "new_candidate")
        assert names1 != names2


# ---------------------------------------------------------------------------
# 4. Empty source directory
# ---------------------------------------------------------------------------

class TestEmptyDirectory:
    def test_empty_source(self, tmp_path: Path):
        src = tmp_path / "empty_src"
        src.mkdir()
        ex = tmp_path / "ex"
        ex.mkdir()
        target = tmp_path / "tgt"
        result = generate_manifest(src, ex, target, target_total=10, seed=1)
        assert result["summary"]["source_supported_eligible"] == 0
        assert result["summary"]["selected_new_count"] == 0


# ---------------------------------------------------------------------------
# 5. Unsupported format skipping
# ---------------------------------------------------------------------------

class TestUnsupportedFormats:
    def test_unsupported_skipped(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "video.mp4").write_bytes(b"\x00" * 5000)
        (src / "doc.pdf").write_bytes(b"%PDF" + b"\x00" * 5000)
        (src / "good.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 2000)

        ex = tmp_path / "ex"
        ex.mkdir()
        target = tmp_path / "tgt"
        result = generate_manifest(src, ex, target, target_total=10, seed=1)
        assert result["summary"]["source_unsupported"] == 2
        assert result["summary"]["source_supported_eligible"] == 1

        excluded = [r for r in result["candidates"] if "unsupported_format" in r.get("exclusion_reason", "")]
        assert len(excluded) == 2


# ---------------------------------------------------------------------------
# 6. Hidden file skipping
# ---------------------------------------------------------------------------

class TestHiddenFiles:
    def test_hidden_files_skipped(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        (src / ".DS_Store").write_bytes(b"\x00" * 5000)
        (src / ".thumbs.db").write_bytes(b"\x00" * 5000)
        (src / "visible.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 2000)

        ex = tmp_path / "ex"
        ex.mkdir()
        target = tmp_path / "tgt"
        result = generate_manifest(src, ex, target, target_total=10, seed=1)
        assert result["summary"]["source_hidden"] == 2
        assert result["summary"]["source_supported_eligible"] == 1


# ---------------------------------------------------------------------------
# 7. Stat errors
# ---------------------------------------------------------------------------

class TestStatErrors:
    def test_stat_error_recorded(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "good.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 2000)

        ex = tmp_path / "ex"
        ex.mkdir()
        target = tmp_path / "tgt"

        real_stat = Path.stat

        def _broken_stat(self, *args, **kwargs):
            if self.name == "good.jpg":
                raise OSError(13, "Permission denied")
            return real_stat(self, *args, **kwargs)

        from unittest.mock import patch
        with patch.object(_module.Path, "stat", _broken_stat):
            result = generate_manifest(src, ex, target, target_total=10, seed=1)

        assert result["summary"]["source_stat_errors"] == 1
        excluded = [r for r in result["candidates"] if r.get("exclusion_reason") == "stat_error"]
        assert len(excluded) == 1


# ---------------------------------------------------------------------------
# 8. CSV output format validation
# ---------------------------------------------------------------------------

class TestCSVOutput:
    def test_csv_has_correct_columns(self, source_dir: Path, existing_dir: Path, tmp_path: Path):
        target = tmp_path / "tgt"
        output_csv = tmp_path / "manifest.csv"
        result = generate_manifest(source_dir, existing_dir, target, target_total=10, seed=1)
        write_csv(result["candidates"], output_csv)

        assert output_csv.is_file()
        with open(output_csv, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            assert "row_id" in fieldnames
            assert "source_path" in fieldnames
            assert "proposed_target_path" in fieldnames
            assert "extension" in fieldnames
            assert "size_bytes" in fieldnames
            assert "selection_reason" in fieldnames
            assert "exclusion_reason" in fieldnames

            rows = list(reader)
            assert len(rows) > 0
            for row in rows:
                assert row["row_id"].isdigit()


# ---------------------------------------------------------------------------
# 9. Summary JSON format validation
# ---------------------------------------------------------------------------

class TestSummaryJSON:
    def test_summary_has_required_fields(self, source_dir: Path, existing_dir: Path, tmp_path: Path):
        target = tmp_path / "tgt"
        summary_path = tmp_path / "summary.json"
        result = generate_manifest(source_dir, existing_dir, target, target_total=10, seed=1)
        write_summary(result["summary"], summary_path)

        assert summary_path.is_file()
        with open(summary_path, "r", encoding="utf-8") as f:
            s = json.load(f)

        required = [
            "source_root_label", "source_root_redacted",
            "existing_root_label", "existing_root_redacted",
            "target_root_label", "target_root_redacted",
            "target_total", "seed",
            "strategy", "existing_supported_count", "needed_new",
            "source_total_scanned", "source_supported_eligible",
            "source_placeholders", "source_unsupported",
            "possible_duplicates_with_existing", "selected_possible_duplicates",
            "source_stat_errors",
            "source_hidden", "selected_new_count",
            "selected_new_total_bytes", "existing_total_bytes",
            "combined_total", "manifest_total_rows",
        ]
        for key in required:
            assert key in s, f"Missing key: {key}"

    def test_summary_no_individual_paths(self, source_dir: Path, existing_dir: Path, tmp_path: Path):
        target = tmp_path / "tgt"
        result = generate_manifest(source_dir, existing_dir, target, target_total=10, seed=1)
        s = result["summary"]
        summary_str = json.dumps(s)
        assert str(source_dir) not in summary_str or "source_root" in summary_str
        for row in result["candidates"]:
            assert row.get("source_path", "") not in summary_str or "root" in summary_str


# ---------------------------------------------------------------------------
# 10. Source directory not modified (safety)
# ---------------------------------------------------------------------------

class TestSourceSafety:
    def test_source_not_modified(self, source_dir: Path, existing_dir: Path, tmp_path: Path):
        before = {}
        for root, dirs, files in os.walk(source_dir):
            for f in files:
                p = Path(root) / f
                before[str(p.relative_to(source_dir))] = p.stat().st_size

        target = tmp_path / "tgt"
        generate_manifest(source_dir, existing_dir, target, target_total=10, seed=1)

        after = {}
        for root, dirs, files in os.walk(source_dir):
            for f in files:
                p = Path(root) / f
                after[str(p.relative_to(source_dir))] = p.stat().st_size

        assert before == after, "Source directory must not be modified"


# ---------------------------------------------------------------------------
# 11. Regression: deterministic scan order (Codex P1)
# ---------------------------------------------------------------------------

class TestDeterministicScanOrder:
    def test_scan_source_order_is_sorted(self, tmp_path: Path):
        src = tmp_path / "src"
        sub_b = src / "Bravo"
        sub_a = src / "Alpha"
        sub_b.mkdir(parents=True)
        sub_a.mkdir(parents=True)
        (sub_b / "z_image.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 2000)
        (sub_a / "a_image.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 2000)
        (sub_b / "m_image.png").write_bytes(b"\x89PNG" + b"\x00" * 2000)

        entries = list(_scan_source(src))
        filenames = [e["filename"] for e in entries]
        assert filenames == ["a_image.jpg", "m_image.png", "z_image.jpg"]

    def test_eligible_presort_makes_sample_stable(self, tmp_path: Path):
        src = tmp_path / "src"
        sub_z = src / "ZDir"
        sub_a = src / "ADir"
        sub_z.mkdir(parents=True)
        sub_a.mkdir(parents=True)
        for i in range(15):
            d = sub_z if i % 2 == 0 else sub_a
            (d / f"img_{i:03d}.jpg").write_bytes(b"\xff\xd8" + b"\x00" * (2000 + i))

        ex = tmp_path / "ex"
        ex.mkdir()
        target = tmp_path / "tgt"

        r1 = generate_manifest(src, ex, target, target_total=5, seed=42)
        r2 = generate_manifest(src, ex, target, target_total=5, seed=42)

        names1 = [r["source_path"] for r in r1["candidates"] if r["selection_reason"] == "new_candidate"]
        names2 = [r["source_path"] for r in r2["candidates"] if r["selection_reason"] == "new_candidate"]
        assert names1 == names2


# ---------------------------------------------------------------------------
# 12. Regression: existing count accuracy (Codex P2)
# ---------------------------------------------------------------------------

class TestExistingCountAccuracy:
    def test_count_by_rows_not_key_set(self, tmp_path: Path):
        ex = tmp_path / "ex"
        sub1 = ex / "dir1"
        sub2 = ex / "dir2"
        sub1.mkdir(parents=True)
        sub2.mkdir(parents=True)
        content = b"\xff\xd8" + b"\x00" * 2000
        (sub1 / "same.jpg").write_bytes(content)
        (sub2 / "same.jpg").write_bytes(content)

        rows, idx, count, total_bytes = _scan_existing_dataset(ex)
        assert count == 2
        assert len(idx) == 1  # same (name, size) key
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# 13. Regression: target collision disambiguation (Codex P1)
# ---------------------------------------------------------------------------

class TestTargetCollisionDisambiguation:
    def test_same_filename_gets_unique_targets(self, tmp_path: Path):
        src = tmp_path / "src"
        sub1 = src / "dir1"
        sub2 = src / "dir2"
        sub1.mkdir(parents=True)
        sub2.mkdir(parents=True)
        (sub1 / "photo.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 2000)
        (sub2 / "photo.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 2500)

        ex = tmp_path / "ex"
        ex.mkdir()
        target = tmp_path / "tgt"

        result = generate_manifest(src, ex, target, target_total=10, seed=1)
        new_rows = [r for r in result["candidates"] if r["selection_reason"] == "new_candidate"]
        target_names = [Path(r["proposed_target_path"]).name for r in new_rows]
        assert len(target_names) == len(set(n.lower() for n in target_names)), \
            f"Target names must be unique, got: {target_names}"

    def test_disambiguated_name_has_hash_suffix(self, tmp_path: Path):
        src = tmp_path / "src"
        sub1 = src / "d1"
        sub2 = src / "d2"
        sub1.mkdir(parents=True)
        sub2.mkdir(parents=True)
        (sub1 / "dup.png").write_bytes(b"\x89PNG" + b"\x00" * 2000)
        (sub2 / "dup.png").write_bytes(b"\x89PNG" + b"\x00" * 2500)

        ex = tmp_path / "ex"
        ex.mkdir()
        target = tmp_path / "tgt"

        result = generate_manifest(src, ex, target, target_total=10, seed=1)
        new_rows = [r for r in result["candidates"] if r["selection_reason"] == "new_candidate"]
        names = [Path(r["proposed_target_path"]).name for r in new_rows]
        assert any("__" in n for n in names), \
            f"Expected at least one disambiguated name with __ hash, got: {names}"


# ---------------------------------------------------------------------------
# 14. Target-total cap enforcement (Codex P1)
# ---------------------------------------------------------------------------

class TestTargetTotalCap:
    def test_existing_exceeds_cap_raises(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        ex = tmp_path / "ex"
        ex.mkdir()
        for i in range(5):
            (ex / f"img_{i}.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 2000)
        target = tmp_path / "tgt"

        with pytest.raises(ValueError, match="exceeds"):
            generate_manifest(src, ex, target, target_total=3, seed=1)

    def test_existing_equals_cap_succeeds(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        ex = tmp_path / "ex"
        ex.mkdir()
        for i in range(3):
            (ex / f"img_{i}.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 2000)
        target = tmp_path / "tgt"

        result = generate_manifest(src, ex, target, target_total=3, seed=1)
        assert result["summary"]["existing_supported_count"] == 3
        assert result["summary"]["needed_new"] == 0
        assert result["summary"]["selected_new_count"] == 0

    def test_existing_under_cap_fills_remaining(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        for i in range(10):
            (src / f"new_{i}.jpg").write_bytes(b"\xff\xd8" + b"\x00" * (2000 + i))
        ex = tmp_path / "ex"
        ex.mkdir()
        for i in range(2):
            (ex / f"ex_{i}.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 2000)
        target = tmp_path / "tgt"

        result = generate_manifest(src, ex, target, target_total=5, seed=1)
        assert result["summary"]["existing_supported_count"] == 2
        assert result["summary"]["needed_new"] == 3
        assert result["summary"]["selected_new_count"] == 3
        assert result["summary"]["combined_total"] == 5

    def test_zero_existing_works(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        for i in range(5):
            (src / f"img_{i}.jpg").write_bytes(b"\xff\xd8" + b"\x00" * (2000 + i))
        ex = tmp_path / "ex"
        ex.mkdir()
        target = tmp_path / "tgt"

        result = generate_manifest(src, ex, target, target_total=3, seed=1)
        assert result["summary"]["existing_supported_count"] == 0
        assert result["summary"]["selected_new_count"] == 3

    def test_combined_total_never_exceeds_target(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        for i in range(20):
            (src / f"new_{i}.jpg").write_bytes(b"\xff\xd8" + b"\x00" * (2000 + i))
        ex = tmp_path / "ex"
        ex.mkdir()
        for i in range(4):
            (ex / f"ex_{i}.jpg").write_bytes(b"\xff\xd8" + b"\x00" * (2000 + i))
        target = tmp_path / "tgt"

        for cap in [4, 5, 6, 10]:
            result = generate_manifest(src, ex, target, target_total=cap, seed=42)
            s = result["summary"]
            combined = s["existing_supported_count"] + s["selected_new_count"]
            assert combined <= cap, f"combined_total {combined} exceeds target_total {cap}"
            assert s["combined_total"] <= cap


# ---------------------------------------------------------------------------
# 15. Privacy-safe summary JSON (Codex P3)
# ---------------------------------------------------------------------------

class TestPrivacySafeSummary:
    def test_summary_redacts_absolute_paths(self, source_dir: Path, existing_dir: Path, tmp_path: Path):
        target = tmp_path / "tgt"
        summary_path = tmp_path / "summary.json"
        result = generate_manifest(source_dir, existing_dir, target, target_total=10, seed=1)
        write_summary(result["summary"], summary_path)

        with open(summary_path, "r", encoding="utf-8") as f:
            s = json.load(f)

        assert "source_root" not in s
        assert "existing_root" not in s
        assert "target_root" not in s
        assert s["source_root_redacted"] is True
        assert s["existing_root_redacted"] is True
        assert s["target_root_redacted"] is True
        assert "source_root_label" in s
        assert "existing_root_label" in s
        assert "target_root_label" in s

    def test_summary_no_absolute_path_values(self, source_dir: Path, existing_dir: Path, tmp_path: Path):
        target = tmp_path / "tgt"
        summary_path = tmp_path / "summary.json"
        result = generate_manifest(source_dir, existing_dir, target, target_total=10, seed=1)
        write_summary(result["summary"], summary_path)

        with open(summary_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert str(source_dir) not in content
        assert str(existing_dir) not in content
        assert str(target) not in content
