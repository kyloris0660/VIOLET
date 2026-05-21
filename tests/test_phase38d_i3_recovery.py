import importlib.util
import os
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "plan_phase38d_i3_recovery.py"
_spec = importlib.util.spec_from_file_location("plan_phase38d_i3_recovery", SCRIPT_PATH)
recovery = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["plan_phase38d_i3_recovery"] = recovery
_spec.loader.exec_module(recovery)


def _protected(label: str, path: Path):
    return recovery.ProtectedRoot(label=label, path=path)


def _protected_roots(tmp_path: Path):
    for dirname in ["source", "repo", "storage"]:
        (tmp_path / dirname).mkdir(exist_ok=True)
    return [
        _protected("source_root", tmp_path / "source"),
        _protected("repo_root", tmp_path / "repo"),
        _protected("app_storage_root", tmp_path / "storage"),
    ]


def _write_log(
    path: Path,
    target: Path,
    expected_count: int,
    *,
    files_copied: int = 1,
    bytes_copied: int | str = 3,
    include_files_copied: bool = True,
    include_bytes_copied: bool = True,
):
    lines = [
        "=== Executing Copy ===",
        f"  Target: {target}",
        f"  Expected files: {expected_count}",
    ]
    if include_files_copied:
        lines.append(f"  Files copied:   {files_copied}")
    if include_bytes_copied:
        lines.append(f"  Bytes copied:   {bytes_copied}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _manifest_rows(tmp_path: Path):
    def row(row_id: int, bucket: str, selected: bool):
        return {
            "row_id": str(row_id),
            "source_path": str(tmp_path / f"source_{row_id}.jpg"),
            "proposed_target_path": str(tmp_path / "target" / f"target_{row_id}.jpg"),
            "extension": ".jpg",
            "size_bytes": "10",
            "selection_reason": "new_candidate" if selected else "",
            "duplicate_key": "",
            "exclusion_reason": "" if selected else "not_selected_temporal_stratified",
            "placeholder_flag": "False",
            "stat_error": "False",
            "temporal_bucket": bucket,
        }

    return [
        row(1, "b01", True),
        row(2, "b02", True),
        row(3, "b02", False),
        row(4, "b01", False),
    ]


def test_final_delivery_report_standard_docs_presence():
    repo = Path(__file__).resolve().parent.parent
    for rel in ["AGENTS.md", "CLAUDE.md", "docs/test-workflow.md"]:
        text = (repo / rel).read_text(encoding="utf-8")
        assert "Final Delivery Report Standard" in text
        assert "PR URL, branch, head SHA" in text
        assert "exact sys.executable" in text
        assert "If any item is not applicable" in text


def test_cleanup_dry_run_refuses_unsafe_target(tmp_path: Path):
    target = tmp_path / "source_root" / "target"
    target.mkdir(parents=True)
    (target / "copied.jpg").write_bytes(b"x")

    public, _local = recovery.build_cleanup_dry_run(
        target_root=target,
        expected_staging_root=tmp_path / "target",
        protected_roots=[
            _protected("source_root", tmp_path / "source_root"),
            _protected("repo_root", tmp_path / "repo"),
            _protected("app_storage_root", tmp_path / "app_storage"),
        ],
        expected_file_count=1,
        expected_total_bytes=1,
        expected_copy_count=1000,
        execute_cleanup_requested=False,
        confirm_cleanup="",
        staging_log=None,
    )

    assert public["status"] == "blocked_unsafe_target"
    assert public["target_is_not_source_icloud"] is False
    assert public["deletion_plan"]["actual_delete_performed"] is False
    assert (target / "copied.jpg").exists()


def test_cleanup_dry_run_does_not_delete_and_requires_confirmation(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    copied = target / "copied.png"
    copied.write_bytes(b"123")
    log = tmp_path / "copy.log"
    _write_log(log, target, 1000)

    public, _local = recovery.build_cleanup_dry_run(
        target_root=target,
        expected_staging_root=target,
        protected_roots=_protected_roots(tmp_path),
        expected_file_count=1,
        expected_total_bytes=3,
        expected_copy_count=1000,
        execute_cleanup_requested=True,
        confirm_cleanup="",
        staging_log=log,
    )

    assert public["status"] == "dry_run_passed"
    assert public["deletion_plan"]["execute_requested"] is True
    assert public["deletion_plan"]["execute_allowed"] is False
    assert public["deletion_plan"]["confirmation_phrase_valid"] is False
    assert public["deletion_plan"]["confirmation_phrase_required"] == recovery.CLEANUP_CONFIRM_PHRASE
    assert copied.exists()


def test_cleanup_report_public_summary_is_privacy_safe(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "secret_file_name.jpg").write_bytes(b"x")
    log = tmp_path / "copy.log"
    _write_log(log, target, 1000)

    public, _local = recovery.build_cleanup_dry_run(
        target_root=target,
        expected_staging_root=target,
        protected_roots=_protected_roots(tmp_path),
        expected_file_count=1,
        expected_total_bytes=1,
        expected_copy_count=1000,
        execute_cleanup_requested=False,
        confirm_cleanup="",
        staging_log=log,
    )
    backfill = recovery.build_backfill_policy(
        manifest_rows=_manifest_rows(tmp_path),
        failed_row_id=2,
        selected_total=4,
    )
    report = recovery.build_recovery_report(
        cleanup_dry_run=public,
        backfill_policy=backfill,
        local_details_artifact="local-details.json",
    )

    text = str(report)
    assert str(tmp_path) not in text
    assert "secret_file_name" not in text
    assert report["privacy"]["passed"] is True


def test_cleanup_byte_mismatch_blocks_dedicated_identity(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    copied = target / "copied.jpg"
    copied.write_bytes(b"x")
    log = tmp_path / "copy.log"
    _write_log(log, target, 1000)

    public, _local = recovery.build_cleanup_dry_run(
        target_root=target,
        expected_staging_root=target,
        protected_roots=_protected_roots(tmp_path),
        expected_file_count=1,
        expected_total_bytes=2,
        expected_copy_count=1000,
        execute_cleanup_requested=False,
        confirm_cleanup="",
        staging_log=log,
    )

    assert public["target_is_dedicated_phase38d_target"] is False
    assert public["dedicated_target_evidence"]["expected_file_count_matches"] is True
    assert public["dedicated_target_evidence"]["expected_total_bytes_matches"] is False
    assert public["status"] == "blocked_identity_mismatch"
    assert "expected_total_bytes_mismatch" in public["identity_mismatch_reasons"]
    assert copied.exists()


def test_cleanup_count_and_byte_match_dedicated_identity(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "copied.jpg").write_bytes(b"abc")
    log = tmp_path / "copy.log"
    _write_log(log, target, 1000)

    public, _local = recovery.build_cleanup_dry_run(
        target_root=target,
        expected_staging_root=target,
        protected_roots=_protected_roots(tmp_path),
        expected_file_count=1,
        expected_total_bytes=3,
        expected_copy_count=1000,
        execute_cleanup_requested=False,
        confirm_cleanup="",
        staging_log=log,
    )

    assert public["target_is_dedicated_phase38d_target"] is True
    assert public["status"] == "dry_run_passed"
    assert public["actual_copied_file_count"] == 1
    assert public["requested_expected_copy_count"] == 1000


def test_cleanup_missing_staging_log_blocks_identity(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "copied.jpg").write_bytes(b"abc")

    public, _local = recovery.build_cleanup_dry_run(
        target_root=target,
        expected_staging_root=target,
        protected_roots=_protected_roots(tmp_path),
        expected_file_count=1,
        expected_total_bytes=3,
        expected_copy_count=1000,
        execute_cleanup_requested=False,
        confirm_cleanup="",
        staging_log=None,
    )

    assert public["target_is_dedicated_phase38d_target"] is False
    assert public["status"] == "blocked_identity_mismatch"
    assert "staging_copy_log_missing" in public["identity_mismatch_reasons"]


def test_cleanup_missing_expected_staging_root_blocks_pass(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "copied.jpg").write_bytes(b"abc")
    log = tmp_path / "copy.log"
    _write_log(log, target, 1000)

    public, _local = recovery.build_cleanup_dry_run(
        target_root=target,
        expected_staging_root=None,
        protected_roots=_protected_roots(tmp_path),
        expected_file_count=1,
        expected_total_bytes=3,
        expected_copy_count=1000,
        execute_cleanup_requested=False,
        confirm_cleanup="",
        staging_log=log,
    )

    assert public["status"] == "blocked_missing_expected_staging_root"
    assert public["expected_staging_root_explicit"] is False
    assert public["target_under_expected_staging_root"] is False
    assert public["target_is_dedicated_phase38d_target"] is False
    assert "expected_staging_root_missing" in public["identity_mismatch_reasons"]


def test_cleanup_wrong_expected_staging_root_blocks_pass(tmp_path: Path):
    target = tmp_path / "target"
    wrong_root = tmp_path / "other_staging"
    target.mkdir()
    wrong_root.mkdir()
    (target / "copied.jpg").write_bytes(b"abc")
    log = tmp_path / "copy.log"
    _write_log(log, target, 1000)

    public, _local = recovery.build_cleanup_dry_run(
        target_root=target,
        expected_staging_root=wrong_root,
        protected_roots=_protected_roots(tmp_path),
        expected_file_count=1,
        expected_total_bytes=3,
        expected_copy_count=1000,
        execute_cleanup_requested=False,
        confirm_cleanup="",
        staging_log=log,
    )

    assert public["status"] == "blocked_identity_mismatch"
    assert public["expected_staging_root_explicit"] is True
    assert public["target_under_expected_staging_root"] is False
    assert "target_not_under_expected_staging_root" in public["identity_mismatch_reasons"]


def test_cleanup_target_root_cannot_validate_itself_as_expected_root(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "copied.jpg").write_bytes(b"abc")
    log = tmp_path / "copy.log"
    _write_log(log, target, 1000)

    public, _local = recovery.build_cleanup_dry_run(
        target_root=target,
        expected_staging_root=None,
        protected_roots=_protected_roots(tmp_path),
        expected_file_count=1,
        expected_total_bytes=3,
        expected_copy_count=1000,
        execute_cleanup_requested=False,
        confirm_cleanup="",
        staging_log=log,
    )

    assert public["status"] != "dry_run_passed"
    assert public["expected_staging_root_explicit"] is False
    assert public["target_under_expected_staging_root"] is False


def test_cleanup_invalid_protected_roots_block_pass(tmp_path: Path):
    target = tmp_path / "target"
    expected_root = tmp_path / "staging"
    target.mkdir()
    expected_root.mkdir()
    (target / "copied.jpg").write_bytes(b"abc")
    log = tmp_path / "copy.log"
    _write_log(log, target, 1000)

    for label in ["source_root", "repo_root", "app_storage_root"]:
        valid_roots = {
            "source_root": tmp_path / "source",
            "repo_root": tmp_path / "repo",
            "app_storage_root": tmp_path / "storage",
        }
        for path in valid_roots.values():
            path.mkdir(exist_ok=True)
        invalid_path = tmp_path / f"missing_{label}"
        valid_roots[label] = invalid_path
        public, _local = recovery.build_cleanup_dry_run(
            target_root=target,
            expected_staging_root=tmp_path,
            protected_roots=[_protected(root_label, root_path) for root_label, root_path in valid_roots.items()],
            expected_file_count=1,
            expected_total_bytes=3,
            expected_copy_count=1000,
            execute_cleanup_requested=False,
            confirm_cleanup="",
            staging_log=log,
        )
        assert public["status"] == "blocked_invalid_protected_root"
        assert label in public["invalid_protected_root_labels"]
        assert public["target_is_dedicated_phase38d_target"] is False


def test_staging_log_expected_count_is_dynamic(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "copied.jpg").write_bytes(b"abc")
    log = tmp_path / "copy.log"
    _write_log(log, target, 500)

    public, _local = recovery.build_cleanup_dry_run(
        target_root=target,
        expected_staging_root=target,
        protected_roots=_protected_roots(tmp_path),
        expected_file_count=1,
        expected_total_bytes=3,
        expected_copy_count=500,
        execute_cleanup_requested=False,
        confirm_cleanup="",
        staging_log=log,
    )
    assert public["status"] == "dry_run_passed"
    assert public["dedicated_target_evidence"]["staging_copy_log_expected_count_correlated"] is True
    assert public["requested_expected_copy_count"] == 500

    public_mismatch, _local = recovery.build_cleanup_dry_run(
        target_root=target,
        expected_staging_root=target,
        protected_roots=_protected_roots(tmp_path),
        expected_file_count=1,
        expected_total_bytes=3,
        expected_copy_count=1000,
        execute_cleanup_requested=False,
        confirm_cleanup="",
        staging_log=log,
    )
    assert public_mismatch["status"] == "blocked_identity_mismatch"
    assert public_mismatch["dedicated_target_evidence"]["staging_copy_log_expected_count_correlated"] is False


def test_staging_log_target_exact_match_not_substring(tmp_path: Path):
    target = tmp_path / "staging"
    wrong_target = tmp_path / "staging-wrong"
    target.mkdir()
    wrong_target.mkdir()
    (target / "copied.jpg").write_bytes(b"abc")
    log = tmp_path / "copy.log"
    _write_log(log, wrong_target, 1000)

    public, _local = recovery.build_cleanup_dry_run(
        target_root=target,
        expected_staging_root=tmp_path,
        protected_roots=_protected_roots(tmp_path),
        expected_file_count=1,
        expected_total_bytes=3,
        expected_copy_count=1000,
        execute_cleanup_requested=False,
        confirm_cleanup="",
        staging_log=log,
    )

    assert public["status"] == "blocked_identity_mismatch"
    assert public["dedicated_target_evidence"]["staging_copy_log_target_exact_match"] is False
    assert public["dedicated_target_evidence"]["staging_copy_log_matches_target"] is False


def test_staging_log_equivalent_normalized_target_matches(tmp_path: Path):
    target = tmp_path / "staging"
    target.mkdir()
    (target / "copied.jpg").write_bytes(b"abc")
    log = tmp_path / "copy.log"
    _write_log(log, target / ".." / "staging", 1000)

    public, _local = recovery.build_cleanup_dry_run(
        target_root=target,
        expected_staging_root=tmp_path,
        protected_roots=_protected_roots(tmp_path),
        expected_file_count=1,
        expected_total_bytes=3,
        expected_copy_count=1000,
        execute_cleanup_requested=False,
        confirm_cleanup="",
        staging_log=log,
    )

    assert public["status"] == "dry_run_passed"
    assert public["dedicated_target_evidence"]["staging_copy_log_target_exact_match"] is True


def test_staging_log_missing_target_line_fails_closed(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "copied.jpg").write_bytes(b"abc")
    log = tmp_path / "copy.log"
    log.write_text("=== Executing Copy ===\n  Expected files: 1000\n", encoding="utf-8")

    public, _local = recovery.build_cleanup_dry_run(
        target_root=target,
        expected_staging_root=tmp_path,
        protected_roots=_protected_roots(tmp_path),
        expected_file_count=1,
        expected_total_bytes=3,
        expected_copy_count=1000,
        execute_cleanup_requested=False,
        confirm_cleanup="",
        staging_log=log,
    )

    assert public["status"] == "blocked_identity_mismatch"
    assert public["dedicated_target_evidence"]["staging_copy_log_target_exact_match"] is False
    assert public["dedicated_target_evidence"]["staging_copy_log_matches_target"] is False


def test_staging_log_count_must_correlate_with_same_target_entry(tmp_path: Path):
    target = tmp_path / "target"
    wrong_target = tmp_path / "wrong_target"
    target.mkdir()
    wrong_target.mkdir()
    (target / "copied.jpg").write_bytes(b"abc")
    log = tmp_path / "copy.log"
    log.write_text(
        "\n".join(
            [
                "=== Executing Copy ===",
                f"  Target: {target}",
                "  Expected files: 500",
                "=== Executing Copy ===",
                f"  Target: {wrong_target}",
                "  Expected files: 1000",
                "",
            ]
        ),
        encoding="utf-8",
    )

    public, _local = recovery.build_cleanup_dry_run(
        target_root=target,
        expected_staging_root=tmp_path,
        protected_roots=_protected_roots(tmp_path),
        expected_file_count=1,
        expected_total_bytes=3,
        expected_copy_count=1000,
        execute_cleanup_requested=False,
        confirm_cleanup="",
        staging_log=log,
    )

    assert public["status"] == "blocked_identity_mismatch"
    assert public["dedicated_target_evidence"]["staging_copy_log_target_exact_match"] is True
    assert public["dedicated_target_evidence"]["staging_copy_log_expected_count_correlated"] is False
    assert public["dedicated_target_evidence"]["staging_copy_log_matches_target"] is False


def test_staging_log_matching_target_count_and_bytes_pass(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "copied.jpg").write_bytes(b"abc")
    log = tmp_path / "copy.log"
    _write_log(log, target, 1000, files_copied=1, bytes_copied=3)

    public, _local = recovery.build_cleanup_dry_run(
        target_root=target,
        expected_staging_root=tmp_path,
        protected_roots=_protected_roots(tmp_path),
        expected_file_count=1,
        expected_total_bytes=3,
        expected_copy_count=1000,
        execute_cleanup_requested=False,
        confirm_cleanup="",
        staging_log=log,
    )

    assert public["status"] == "dry_run_passed"
    assert public["dedicated_target_evidence"]["staging_copy_log_matches_target"] is True
    assert public["dedicated_target_evidence"]["staging_copy_log_files_copied_matches"] is True
    assert public["dedicated_target_evidence"]["staging_copy_log_bytes_copied_matches"] is True


def test_staging_log_matching_target_with_wrong_copied_count_fails(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "copied.jpg").write_bytes(b"abc")
    log = tmp_path / "copy.log"
    _write_log(log, target, 1000, files_copied=2, bytes_copied=3)

    public, _local = recovery.build_cleanup_dry_run(
        target_root=target,
        expected_staging_root=tmp_path,
        protected_roots=_protected_roots(tmp_path),
        expected_file_count=1,
        expected_total_bytes=3,
        expected_copy_count=1000,
        execute_cleanup_requested=False,
        confirm_cleanup="",
        staging_log=log,
    )

    assert public["status"] == "blocked_identity_mismatch"
    assert public["dedicated_target_evidence"]["staging_copy_log_files_copied_matches"] is False


def test_staging_log_target_expected_only_fails_incomplete(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "copied.jpg").write_bytes(b"abc")
    log = tmp_path / "copy.log"
    _write_log(log, target, 1000, include_files_copied=False, include_bytes_copied=False)

    public, _local = recovery.build_cleanup_dry_run(
        target_root=target,
        expected_staging_root=tmp_path,
        protected_roots=_protected_roots(tmp_path),
        expected_file_count=1,
        expected_total_bytes=3,
        expected_copy_count=1000,
        execute_cleanup_requested=False,
        confirm_cleanup="",
        staging_log=log,
    )

    assert public["status"] == "blocked_incomplete_staging_log"
    assert "staging_copy_log_files_copied_missing" in public["identity_mismatch_reasons"]
    assert "staging_copy_log_bytes_copied_missing" in public["identity_mismatch_reasons"]
    assert public["dedicated_target_evidence"]["staging_copy_log_matches_target"] is False


def test_staging_log_missing_bytes_fails_incomplete(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "copied.jpg").write_bytes(b"abc")
    log = tmp_path / "copy.log"
    _write_log(log, target, 1000, include_bytes_copied=False)

    public, _local = recovery.build_cleanup_dry_run(
        target_root=target,
        expected_staging_root=tmp_path,
        protected_roots=_protected_roots(tmp_path),
        expected_file_count=1,
        expected_total_bytes=3,
        expected_copy_count=1000,
        execute_cleanup_requested=False,
        confirm_cleanup="",
        staging_log=log,
    )

    assert public["status"] == "blocked_incomplete_staging_log"
    assert "staging_copy_log_bytes_copied_missing" in public["identity_mismatch_reasons"]


def test_staging_log_missing_copied_count_fails_incomplete(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "copied.jpg").write_bytes(b"abc")
    log = tmp_path / "copy.log"
    _write_log(log, target, 1000, include_files_copied=False)

    public, _local = recovery.build_cleanup_dry_run(
        target_root=target,
        expected_staging_root=tmp_path,
        protected_roots=_protected_roots(tmp_path),
        expected_file_count=1,
        expected_total_bytes=3,
        expected_copy_count=1000,
        execute_cleanup_requested=False,
        confirm_cleanup="",
        staging_log=log,
    )

    assert public["status"] == "blocked_incomplete_staging_log"
    assert "staging_copy_log_files_copied_missing" in public["identity_mismatch_reasons"]


def test_gb_byte_tolerance_uses_two_decimal_precision(tmp_path: Path):
    gib = 1024 * 1024 * 1024
    target = tmp_path / "target"
    log = tmp_path / "copy.log"
    _write_log(log, target, 1000, bytes_copied="1.00 GB")

    within_tolerance = recovery._staging_log_matches_target(
        log,
        target,
        expected_copy_count=1000,
        expected_file_count=1,
        expected_total_bytes=int(round(1.004 * gib)),
        relative_target_bases=[tmp_path],
    )
    outside_tolerance = recovery._staging_log_matches_target(
        log,
        target,
        expected_copy_count=1000,
        expected_file_count=1,
        expected_total_bytes=int(round(1.10 * gib)),
        relative_target_bases=[tmp_path],
    )

    assert within_tolerance.log_matches is True
    assert outside_tolerance.log_matches is False
    assert outside_tolerance.bytes_copied_matches is False


def test_kb_mb_and_byte_tolerances(tmp_path: Path):
    target = tmp_path / "target"
    log = tmp_path / "copy.log"
    _write_log(log, target, 1000, bytes_copied="1.0 KB")
    assert recovery._staging_log_matches_target(
        log,
        target,
        expected_copy_count=1000,
        expected_file_count=1,
        expected_total_bytes=1024 + 50,
        relative_target_bases=[tmp_path],
    ).log_matches is True
    assert recovery._staging_log_matches_target(
        log,
        target,
        expected_copy_count=1000,
        expected_file_count=1,
        expected_total_bytes=1024 + 70,
        relative_target_bases=[tmp_path],
    ).log_matches is False

    _write_log(log, target, 1000, bytes_copied="1.0 MB")
    assert recovery._staging_log_matches_target(
        log,
        target,
        expected_copy_count=1000,
        expected_file_count=1,
        expected_total_bytes=1024 * 1024 + 52_000,
        relative_target_bases=[tmp_path],
    ).log_matches is True

    _write_log(log, target, 1000, bytes_copied="3 B")
    assert recovery._staging_log_matches_target(
        log,
        target,
        expected_copy_count=1000,
        expected_file_count=1,
        expected_total_bytes=3,
        relative_target_bases=[tmp_path],
    ).log_matches is True
    assert recovery._staging_log_matches_target(
        log,
        target,
        expected_copy_count=1000,
        expected_file_count=1,
        expected_total_bytes=4,
        relative_target_bases=[tmp_path],
    ).log_matches is False


def test_relative_target_resolves_against_stable_expected_root_parent(tmp_path: Path):
    target = tmp_path / "staging"
    target.mkdir()
    (target / "copied.jpg").write_bytes(b"abc")
    log = tmp_path / "copy.log"
    _write_log(log, Path("staging"), 1000)
    other_cwd = tmp_path / "other_cwd"
    other_cwd.mkdir()
    previous_cwd = Path.cwd()
    try:
        os.chdir(other_cwd)
        public, _local = recovery.build_cleanup_dry_run(
            target_root=target,
            expected_staging_root=target,
            protected_roots=_protected_roots(tmp_path),
            expected_file_count=1,
            expected_total_bytes=3,
            expected_copy_count=1000,
            execute_cleanup_requested=False,
            confirm_cleanup="",
            staging_log=log,
        )
    finally:
        os.chdir(previous_cwd)

    assert public["status"] == "dry_run_passed"
    assert public["dedicated_target_evidence"]["relative_target_handling"] == "stable_base"


def test_relative_target_resolves_against_expected_staging_root(tmp_path: Path):
    expected_root = tmp_path / "expected_staging_root"
    target = expected_root / "phase_3_8d"
    target.mkdir(parents=True)
    (target / "copied.jpg").write_bytes(b"abc")
    log = tmp_path / "copy.log"
    _write_log(log, Path("phase_3_8d"), 1000)

    public, _local = recovery.build_cleanup_dry_run(
        target_root=target,
        expected_staging_root=expected_root,
        protected_roots=_protected_roots(tmp_path),
        expected_file_count=1,
        expected_total_bytes=3,
        expected_copy_count=1000,
        execute_cleanup_requested=False,
        confirm_cleanup="",
        staging_log=log,
    )

    assert public["status"] == "dry_run_passed"
    assert public["dedicated_target_evidence"]["relative_target_handling"] == "stable_base"


def test_ambiguous_relative_target_fails_closed(tmp_path: Path):
    target = tmp_path / "staging"
    log = tmp_path / "copy.log"
    _write_log(log, Path("staging"), 1000)

    match = recovery._staging_log_matches_target(
        log,
        target,
        expected_copy_count=1000,
        expected_file_count=1,
        expected_total_bytes=3,
        relative_target_bases=[],
    )

    assert match.log_matches is False
    assert match.relative_target_handling == "ambiguous_relative_target_failed_closed"


def test_relative_manifest_target_paths_normalize_against_staging_target(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    copied = target / "copied.jpg"
    copied.write_bytes(b"abc")
    log = tmp_path / "copy.log"
    _write_log(log, target, 1000)
    rows = [
        {
            "row_id": "1",
            "source_path": str(tmp_path / "source.jpg"),
            "proposed_target_path": "copied.jpg",
            "extension": ".jpg",
            "size_bytes": "3",
            "selection_reason": "new_candidate",
            "duplicate_key": "",
            "exclusion_reason": "",
            "placeholder_flag": "False",
            "stat_error": "False",
            "temporal_bucket": "b01",
        }
    ]
    other_cwd = tmp_path / "other_cwd"
    other_cwd.mkdir()
    previous_cwd = Path.cwd()
    try:
        os.chdir(other_cwd)
        public, _local = recovery.build_cleanup_dry_run(
            target_root=target,
            expected_staging_root=target,
            protected_roots=_protected_roots(tmp_path),
            expected_file_count=1,
            expected_total_bytes=3,
            expected_copy_count=1000,
            expected_manifest_rows=rows,
            execute_cleanup_requested=False,
            confirm_cleanup="",
            staging_log=log,
        )
    finally:
        os.chdir(previous_cwd)

    assert public["status"] == "dry_run_passed"
    assert public["dedicated_target_evidence"]["unexpected_files_check_passed"] is True


def test_custom_local_details_label_is_propagated_without_full_path(tmp_path: Path):
    cleanup = {"status": "dry_run_passed"}
    backfill = recovery.build_backfill_policy(
        manifest_rows=_manifest_rows(tmp_path),
        failed_row_id=2,
        selected_total=4,
    )

    default_report = recovery.build_recovery_report(
        cleanup_dry_run=cleanup,
        backfill_policy=backfill,
        local_details_artifact=recovery.DEFAULT_LOCAL_DETAILS_JSON.name,
    )
    custom_report = recovery.build_recovery_report(
        cleanup_dry_run=cleanup,
        backfill_policy=backfill,
        local_details_artifact=(tmp_path / "custom-local-details.json").name,
    )

    assert default_report["local_artifacts"]["local_details_artifact"] == "phase-3.8d-i3-recovery-local-details.json"
    assert custom_report["local_artifacts"]["local_details_artifact"] == "custom-local-details.json"
    assert str(tmp_path) not in str(custom_report)


def test_read_probe_policy_is_opt_in():
    policy = recovery.controlled_read_probe_policy()

    assert policy["default_enabled"] is False
    assert policy["approval_required_before_run"] is True
    assert policy["may_trigger_provider_hydration"] is True
    assert policy["cfhydrateplaceholder"]["not_implemented_in_phase_3_8d_i3"] is True


def test_backfill_policy_preserves_bucket_distribution(tmp_path: Path):
    policy = recovery.build_backfill_policy(
        manifest_rows=_manifest_rows(tmp_path),
        failed_row_id=2,
        selected_total=4,
    )

    dry_run = policy["dry_run_plan"]
    assert policy["dry_run_only"] is True
    assert policy["actual_manifest_replacement_performed"] is False
    assert dry_run["selected_total_preserved"] is True
    assert dry_run["replacement_count"] == 1
    assert dry_run["replacements"][0]["bucket"] == "b02"
    assert dry_run["replacements"][0]["replacement_row_id"] == 3
